import inspect
import logging

from io import IOBase
from functools import partial
from decorator import decorator
from types import MappingProxyType
from collections.abc import Callable, Iterable
from typing import TypeVar, ParamSpec, Concatenate, Literal, Any

from ._cache import Cache, ThreadCache, ProcessCache


T = TypeVar('T')
P = ParamSpec('P')
Scope = Literal['thread', 'process']


def _hash_content(obj) -> int:
    typename = type(obj).__name__
    if isinstance(obj, logging.getLoggerClass()):
        obj = obj.name
    elif hasattr(obj, '__dict__'):
        if isinstance(obj.__dict__, MappingProxyType):
            objdict = {k: v for k, v in obj.__dict__.items() if not k.startswith('__')}
        else:
            objdict = obj.__dict__.copy()
        objdict['___name'] = getattr(obj, '__name__', None)
        objdict['___classname'] = typename
        if isinstance(obj, dict):
            obj = obj.copy()
            obj['___objdict'] = objdict
        else:
            obj = objdict
    if isinstance(obj, dict):
        obj = frozenset((k, _hash_content(v)) for k, v in obj.items())
    elif isinstance(obj, Iterable) and not isinstance(obj, str | bytes | bytearray | IOBase):
        obj = tuple(_hash_content(x) for x in obj)
    return hash((obj, typename))


def _cached_call_impl(
        scope: Scope,
        name: str,
        callfunc: Callable[P, T],
        init_method: Callable[Concatenate[Any, P], T] | None,
        *args: P.args,
        **kwargs: P.kwargs,
) -> T:
    match scope:
        case 'thread':
            cache_class = ThreadCache
        case 'process':
            cache_class = partial(ProcessCache, n_locks=100)
        case _:
            raise ValueError(f"Invalid scope '{scope}'")
    if init_method is None:
        bound_args = inspect.signature(callfunc).bind(*args, **kwargs)
    else:
        # Add an extra argument for 'self'
        bound_args = inspect.signature(init_method).bind(None, *args, **kwargs)
    key = _hash_content(bound_args.arguments)
    cache: Cache[int, T] = cache_class(name)
    if key not in cache:
        with cache.lock(key):
            if key not in cache:
                cache[key] = callfunc(*args, **kwargs)
    return cache[key]


def cached_call(callable_obj: Callable[P, T], *args: P.args, scope: Scope = 'process',
                cache_name: str | None = None, **kwargs: P.kwargs) -> T:
    """
    Call an object and get the same object for repeated calls with the same arguments.

    Parameters
    ----------
    callable_obj : callable
        The object to call.

    scope : {"thread", "process"}, default "process"
        The scope of the cache.
        If "thread", there is a different cache for each thread.
        If "process", there is a single global cache, shared by all threads.

    cache_name : string (optional)
        Unique name for the cache used. Default is the name of the callable.

    *args, **kwargs :
        Sent to the callable object.
        These are the values determining the key to the cache.

    Returns
    -------
    object
        The return value of the calling `callable_obj` with `args` and `kwargs`.
    """
    return _cached_call_impl(scope, cache_name or callable_obj.__name__, callable_obj, None, *args, **kwargs)


@decorator
def cached(callable_obj: Callable[P, T], scope: Scope = 'process', cache_name: str | None = None,
           *args: P.args, **kwargs: P.kwargs) -> T:
    """
    A decorator version of `cached_call`.
    Mark a callable as cached, based on the arguments of the call.

    Parameters
    ----------
    callable_obj : callable
        The object to call.

    scope : {"thread", "process"}, default "process"
        The scope of the cache.
        If "thread", there is a different cache for each thread.
        If "process", there is a single global cache, shared by all threads.

    cache_name : string (optional)
        Unique name for the cache used. Default is the name of the callable.

    *args, **kwargs :
        Sent to the callable object.
        These are the values determining the key to the cache.

    Returns
    -------
    A decorator for marking functions as cached.

    Notes
    -----
    Do NOT use as a class decorator. Instead, use the `Cached` metaclass.
    """
    return cached_call(callable_obj, scope=scope, cache_name=cache_name, *args, **kwargs)


class Cached(type):
    """
    A metaclass marking the class as cached.
    Repeated initializations with the same parameters will return the same object.

    Parameters
    ----------
    scope : {"thread", "process"}, default "process"
        The scope of the cache.
        If "thread", there is a different cache for each thread.
        If "process", there is a single global cache, shared by all threads.

    Examples
    --------
    Parameters to a metaclass should be provided as keyword arguments when stating the metaclass:

    >>> class MyClass(metaclass=Cached, scope='thread'):
    ...     # Implementation
    ...     ...
    """
    def __new__(mcs, name, bases, attrs, **kwargs):
        return super().__new__(mcs, name, bases, attrs)

    def __init__(cls, name, bases, attrs, scope: Scope = 'process'):
        super().__init__(name, bases, attrs)
        cls._cache_scope = scope

    def __call__(cls, *args, **kwargs):
        return _cached_call_impl(cls._cache_scope, cls.__name__, super().__call__, cls.__init__, *args, **kwargs)
