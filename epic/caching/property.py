import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar, Generic, Callable, overload, Any

from epic.common.io import pload, pdump

from ._cache import ProcessCache, KT, VT


OC = TypeVar('OC', bound="_OwnedCache")


class _OwnedCache(Generic[KT, VT]):
    def __init__(self, owner, key: KT):
        self.cache = ProcessCache[KT, VT]('property_cache', owner.__dict__)
        self.key = key

    def retrieve(self) -> VT:
        return self.cache[self.key]

    def insert(self, item: VT) -> None:
        self.cache[self.key] = item

    def clear(self) -> None:
        if self.key in self.cache:
            del self.cache[self.key]

    def full(self) -> bool:
        return self.key in self.cache

    @contextmanager
    def lock(self: OC) -> Iterator[OC]:
        with self.cache.lock(self.key):
            yield self


T_co = TypeVar('T_co', covariant=True)
T_contra = TypeVar('T_contra', contravariant=True)
CP = TypeVar('CP', bound="cached_property")


# noinspection PyPep8Naming
class cached_property(Generic[T_contra, T_co]):
    # noinspection PyUnresolvedReferences
    """
    A property decorator with automatic caching.

    Meant to be used within a class definition on a method, similarly to the builtin `property` decorator.
    The property can also be set and deleted.
    All operations on the property are thread-safe.

    Parameters
    ----------
    *dependencies : str, optional
        Names of members on which the value of the property depends.
        If the values of these members haven't changed since last use, the result is retrieved from the cache.
        Otherwise, it is recalculated.

    Notes
    -----
    Only a single value is saved in the cache at each moment. When the dependencies change, a new value
    is calculated (on the next access to the property), and it *replaces* the previously saved value.
    That is, there is a single "property", yet it depends on the state of the instance.
    If multiple saved values are required, use the `cached` decorator on a method with parameters.

    Examples
    --------
    >>> class MyClass:
    ...   def __init__(self, x):
    ...     self.x = x
    ...
    ...   @cached_property
    ...   def y(self):
    ...     return expensive_computation()
    ...
    ...   @cached_property('x')
    ...   def z(self):
    ...     return expensize_computation(self.x)

    Instances of MyClass have two properties, `y` and `z`.
    Upon first access to `y`, the expensive computation is carried out, but not on further invocations.
    Since the value of `z` depends on `x`, the expensive computation will be carried out only when `x` changes.
    """
    def __init__(self, method_or_dependency: Callable[[T_contra], T_co] | str | None = None, *dependencies: str):
        if isinstance(method_or_dependency, str):
            # decorator factory mode, must decorate later using `__call__`
            self.function = None
            dependencies = method_or_dependency, *dependencies
        else:
            # either decorator mode, or an empty decorator factory
            self.function = method_or_dependency
        self.dependencies = frozenset(dependencies)

    def __call__(self: CP, func: Callable[[T_contra], T_co]) -> CP:
        """Decorate a method."""
        self.function = func
        return self

    @property
    def __doc__(self):
        return self.function.__doc__

    @property
    def name(self) -> str:
        return self.function.__name__

    def get_cache(self, owner: T_contra) -> _OwnedCache[str, tuple[tuple, T_co]]:
        return _OwnedCache(owner, self.name)

    @staticmethod
    def state_without_cache(owner: T_contra):
        """
        Get the state of the owner, without the cache.

        This is useful for serialization of objects with cached properties.
        To be used inside __getstate__. The state is retrieved as if super().__getstate__()
        was called, or as the __dict__ of the owner.
        If the state is a dictionary, a copy is made and the cache is removed from it.

        Parameters
        ----------
        owner : object
            An instance with some cached properties.

        Returns
        -------
        object
            The state of the owner.

        Examples
        --------
        >>> class MyClass:
        ...   # cached_property is used at least once in the class definition
        ...   ...
        ...
        ...   def __getstate__(self):
        ...     return cached_property.state_without_cache(self)

        Now instances of MyClass can be properly serialized without their cache.
        After dezerialization, the cached properties would have to be recalculated upon first access.
        """
        parent = owner
        owner_getstate = getattr(getattr(owner, '__getstate__', None), '__func__', False)
        while getattr(getattr(parent, '__getstate__', None), '__func__', None) is owner_getstate:
            parent = super(type(parent), parent)
        if hasattr(parent, '__getstate__'):
            state = parent.__getstate__()
        elif hasattr(owner, '__dict__'):
            state = owner.__dict__
        else:
            raise RuntimeError(f"Cannot get state of {owner}")
        if isinstance(state, dict) and ProcessCache.STORE_NAME in state:
            state = state.copy()
            del state[ProcessCache.STORE_NAME]
        return state

    def dependency_values(self, owner: T_contra) -> tuple:
        return tuple(getattr(owner, x) for x in self.dependencies)

    def is_cache_valid(self, owner: T_contra) -> bool:
        """Test whether the current values of the dependencies match those stored in the cache."""
        cache = self.get_cache(owner)
        if not cache.full():
            return False
        with cache.lock():
            if not cache.full():
                return False
            return cache.retrieve()[0] == self.dependency_values(owner)

    @overload
    def __get__(self: CP, owner: None, owner_type) -> CP: ...
    @overload
    def __get__(self, owner: T_contra, owner_type=None) -> T_co: ...

    def __get__(self, owner, owner_type=None):
        """Get the value, either from the cache or by calculating it."""
        if owner is None:
            return self
        # Must get the dependency values before the lock, since the dependencies could
        # themselves be cached properties.
        dep_values = self.dependency_values(owner)
        with self.get_cache(owner).lock() as cache:
            cached_dep_values, result = cache.retrieve() if cache.full() else (None, None)
            if dep_values != cached_dep_values:
                result = self.function(owner)
                cache.insert((dep_values, result))
            return result

    def __set__(self, owner: T_contra, value: T_co) -> None:
        """Put a value into the cache directly."""
        with self.get_cache(owner).lock() as cache:
            cache.insert((self.dependency_values(owner), value))

    def __delete__(self, owner: T_contra) -> None:
        """Clear the cache."""
        cache = self.get_cache(owner)
        if cache.full():
            with cache.lock():
                cache.clear()


class _OwnedPicklerCache(_OwnedCache[KT, VT]):
    def __init__(self, owner: T_contra, key: KT, parent: "pickled_cached_property[T_contra, Any]"):
        super().__init__(owner, key)
        self.owner = owner
        self.parent = parent

    @property
    def filename(self) -> str:
        return self.parent.get_filename(self.owner)

    def retrieve(self) -> VT:
        if super().full():
            return super().retrieve()
        filename = self.filename
        if os.path.exists(filename):
            item = pload(filename)
            super().insert(item)
            return item
        raise KeyError(self.key)

    def insert(self, item: VT) -> None:
        super().insert(item)
        pdump(item, self.filename)

    def clear(self) -> None:
        filename = self.filename
        if os.path.exists(filename):
            os.remove(filename)
        super().clear()

    def full(self) -> bool:
        return super().full() or os.path.exists(self.filename)


# noinspection PyPep8Naming
class pickled_cached_property(cached_property[T_contra, T_co]):
    # noinspection PyUnresolvedReferences
    """
    A cached property which also uses a file as a secondary cache source,
    allowing for the value to persist between different instances, or even different runs.

    Whenever the value is calculated, it, and the current dependency values, are also pickled
    and dumped into the file provided. When the value is retrieved, if it is in the cache, it
    is retrieved from there. If not, and a file with the given filename exists, the value is
    loaded from the file. Only if a file does not exist (yet), the actual calculation is performed.

    If there are dependencies, they can be incorporated into the filename, resulting in the creation
    of different files for different values of the dependencies (see example below).

    Parameters
    ----------
    *dependencies : str, optional
        Names of members on which the value of the property depends.
        If the values of these members haven't changed since last use, the result is retrieved from the cache.
        Otherwise, it is recalculated.

    filename : str
        The name of the file in which to store the value.
        Can refer to the dependencies using the `format` syntax (i.e. "{...}"), resulting in different
        files for different values of the dependencies.

    Examples
    --------
    >>> class MyClass:
    ...   def __init__(self, x):
    ...     self.x = x
    ...
    ...   @pickled_cached_property(filename="y.pkl")
    ...   def y(self):
    ...     return expensive_computation()
    ...
    ...   @pickled_cached_property('x', filename="saved_for_{x}.pkl")
    ...   def z(self):
    ...     return expensize_computation(self.x)

    The value for `y` will be saved in "y.pkl", and available pre-calculated on the next fresh run, when the
    cache in memory is still empty. It will also be availavle for other instances of `MyClass`.
    Including "{x}" in the filename for `z` allows values of `z` for different values of `x` to be saved
    in separate files.
    """
    def __init__(self, *dependencies: str, filename: str):
        super().__init__(*dependencies)
        self.filename = filename

    def get_filename(self, owner: T_contra) -> str:
        return self.filename.format(**dict(zip(self.dependencies, self.dependency_values(owner))))

    def get_cache(self, owner: T_contra) -> _OwnedPicklerCache[str, tuple[tuple, T_co]]:
        return _OwnedPicklerCache(owner, self.name, self)


# noinspection PyPep8Naming
class lazy_property(cached_property[T_contra, T_co]):
    # noinspection PyUnresolvedReferences
    """
    A cached property that is not calculated until all its dependencies are not None.
    Until then, returns None.

    Parameters
    ----------
    *dependencies : str, optional
        Names of members on which the value of the property depends.
        If any of these members is None or not set, the result is None.
        Otherwise, if the values of these members haven't changed since last use, the result
        is retrieved from the cache. If they have, the result is recalculated.
    """

    @overload
    def __get__(self: CP, owner: None, owner_type) -> CP: ...
    @overload
    def __get__(self, owner: T_contra, owner_type=None) -> T_co | None: ...

    def __get__(self, owner, owner_type=None):
        """Get the value, either from the cache or by calculating it."""
        if owner is None:
            return self
        for member in self.dependencies:
            if getattr(owner, member, None) is None:
                return
        return super().__get__(owner, owner_type)
