# Epic caching - Utilities for caching
[![Epic-caching CI](https://github.com/Cybereason/epic-caching/actions/workflows/ci.yml/badge.svg)](https://github.com/Cybereason/epic-caching/actions/workflows/ci.yml)


## What is it?
The **epic-caching** Python library provides several utilities which help in getting previously
calculated values or previously created objects. It has some overlap with the standard library
`@functools.cache` and `@functools.cached_property`, albeit with some additional functionality.


## Usage

The utilities in the library allow for three types of functionalities:
- Declaring a class as a Singleton
- Caching the results of a function or the instances of a class by arguments, either on a 
single-call basis or always.
- Adding flexible cached properties to classes.


### Make a class into a Singleton

The `Singleton` metaclass transforms a class into a singleton:
```python
from epic.caching import Singleton

class MySingleton(metaclass=Singleton):
    ...
```
This means that there exists at-most
a single instance of the class. On first instantiation, the initialization parameters are used, and
on additional calls, they are ignored and the single instance is returned.


### Cache class instances or the results of function calls

While the `Singleton` metaclass ensures a single instance, sometimes we need the same instance
to be created if the same initialization arguments are given, but a different instance for different arguments.
This behavior is similar to what `@functools.cache` does. The library extends this functionality in two ways:
- It allows some calls to the function or class to be cached but not others.
- It can cache the results in one of two "scopes", either a single cache shared by all threads, or 
a separate cache for each thread.

To only cache a call to a callable object, use the `cached_call` function. It receives the `*args` and `**kwargs`
to send to the callable, as well as an optional `scope` parameter (either `"process"` or `"thread"`), 
specifying the scope of the cache:
```python
import random
from epic.caching import cached_call

def func(x):
    return x + random.random()

assert cached_call(func, 3) is cached_call(func, 3)
```

To cache all calls to a function, use the `cached` decorator, which also receives an optional `scope`:
```python
import random
from epic.caching import cached

@cached
def func(x):
    return x + random.random()

assert func(4) is func(4)
```
If instead we used `@cached(scope='thread')`, repeated calls with the same arguments within the same thread would
return the same object, but not in different threads.

To cache all instances of a class, use the `Cached` metaclass:
```python
from epic.caching import Cached

class A(metaclass=Cached):
    def __init__(self, x):
        self.x = x

assert A(5) is A(5)
assert A(6) is not A(5)
```
Had we used the `Singleton` metaclass, the second assertion would have failed, since there would be only a single
instance of `A`. With the `Cached` metaclass, the same arguments return the same instance, but different arguments
return different instances.

The `Cached` metaclass can also be given a `scope`:
```python
from epic.caching import Cached

class B(metaclass=Cached, scope='thread'):
    ...
```
The behavior is similar to those of `cached_call` and `cached`.


### Cached properties
The library provides a `cached_property` decorator, which is very similar to the standard one from `functools`.
However, it provides some functionalities absent from the standard decorator:
- It is possible to provide a set of _dependencies_ for the property. These are the names of members on which
the value of the property depends. When the cached property is accessed, these members are accessed. If their
values have changed since last access, the property is recalculated. If not, the cached value is returned.
Note that, contrary to the caching mechanisms described above, we do not cache multiple values for the property
(one for each set of dependency values). Only a single value is cached at any given moment. If the dependencies
change, the new value is calculated and stored in the cache, replacing of the old one.
- The cached property can also be set and deleted. When the property is set, the given value is also stored in
the cache. When it is deleted, the cache is cleared.
- The cached property can be used by multiple threads. The cache is shared by all threads and the operations
on the property are thread safe.

The usage of the property is trivial:
```python
from epic.caching import cached_property

class ClassWithCachedProp:
    @cached_property
    def prop(self):
        return self.expensive_computation()

instance = ClassWithCachedProp()
assert instance.prop is instance.prop
instance.prop = 3.14
del instance.prop
```
In the line beginning with `assert`, the expensive computation is carried out only once.

To specify dependencies, provide the member names as strings to the `cached_property` constructor:
```python
from epic.caching import cached_property

class AnotherClassWithCachedProp:
    def __init__(self, x):
        self.x = x

    @cached_property('x')
    def prop(self):
        return self.x + self.expensive_computation()
```
By declaring this dependency, now every time `self.prop` is accessed, if the value of `self.x` has changed, `prop`
would need to be recalculated.

To check explicitly whether the stored dependency values match the current member values, use the method
`is_cache_valid`. Note that in order to do so, the property must be accessed using the host class, not its instances:
```python
instance = AnotherClassWithCachedProp(42)
if AnotherClassWithCachedProp.prop.is_cache_valid(instance):
    ...
```

#### Serialization
It is often desired to serialize objects without their stored cache values. The reason is usually to save space,
since the stored values do not constitute "part of the object", just an optimization implementation. In order to
remove the cache when serializing, we need to define the `__getstate__` method. In it, we can get the state of the
object without its cache using the `state_without_cache` static method:
```python
from epic.caching import cached_property

class Persistent:
    # cached_property is used at least once in the class definition
    ...

    def __getstate__(self):
        return cached_property.state_without_cache(self)
```
The returned state is the object's `__dict__`, or the result of its base class `__getstate__` method, if it exists.
If the state is a dictionary, it is copied and the cache is removed from the copy. Of course, if other manipulations
on the state should be performed during serialization, they can be done so in `__getstate__` on the returned value
before returning it.

#### Secondary cache in a file
Sometimes it can be useful to have the value of the property persist between runs of the program, or between processes.
To do so, we can automatically save a cached property value in a file. This file would hold the (pickled) value of
a single property. Generally, this makes sense only if we plan to have only a single instance of the class,
as multiple instances would share the same cache file. Note that the pickle file acts as a _secondary_ cache, in
addition to the cache in memory: if the value cannot be found in the memory cache, it is searched for in the file.
When a value is cached, it is saved both in memory and in file.

To define a cached property with a secondary file cache, use the `pickled_cached_property` decorator. It is a
subclass of `cached_property` that, in addition to optional dependencies, must get a `filename` parameter:
```python
from epic.caching import pickled_cached_property

class PersistsToFile:
    def __init__(self, x):
        self.x = x
        
    @pickled_cached_property('x', filename='prop_cache.pkl')
    def prop(self):
        return self.x + self.expensive_computation()
```
Of course, as a subclass of `cached_property`, the pickled property can also be set or deleted.

If there are dependencies, they can be incorporated into the filename, using the `format` syntax (i.e. `{...}`),
resulting in the creation of different files for different values of the dependencies:
```python
from epic.caching import pickled_cached_property

class PersistsToFile:
    def __init__(self, x, workdir='.'):
        self.x = x
        self.workdir = workdir
        
    @pickled_cached_property('x', 'workdir', filename='{workdir}/prop_cache_for_{x}.pkl')
    def prop(self):
        return self.x + self.expensive_computation()
```

#### Lazy properties
A lazy property is a cached property that should only be calculated when all of its dependencies are "set".
In this context, a member is considered "set" when it exists and its value is not `None`. Until all the dependencies
are set, the value of the lazy property itself is `None`:
```python
from epic.caching import lazy_property

class Lazy:
    @lazy_property('x')
    def y(self):
        return self.x + 1

lazy = Lazy()
assert lazy.y is None
lazy.x = 1
assert lazy.y is 2
```
The `lazy_property` decorator is a subclass of `cached_property`. Note that without any dependencies, a lazy
property is no different from a cached property. Of course, after all the dependencies are set and the lazy property
is calculated, its value is cached as usual.
