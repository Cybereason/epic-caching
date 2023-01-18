from typing import TypeVar


T = TypeVar('T')


class Singleton(type):
    """
    A metaclass marking the class as a Singleton.
    A Singleton class can be initialized as usual, but the same instance is returned every time.

    Note that you are allowed to pass initialization parameters when getting the instance. In the first invocation,
    these parameters will be passed to the class `__init__` method; in subsequent invocations, they are ignored.

    To get a different instance for different initialization parameters, use the `Cached` metaclass instead.
    """
    def __init__(cls, name, bases, attrs):
        super().__init__(name, bases, attrs)
        cls._instance = None

    def __call__(cls: type[T], *args, **kwargs) -> T:
        if cls._instance is None:
            cls._instance = super().__call__(*args, **kwargs)
        return cls._instance
