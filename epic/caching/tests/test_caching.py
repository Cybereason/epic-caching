import os
import pickle
import pytest
import random
import operator
import threading
from functools import partial

from epic.caching import (
    Singleton,
    cached_call,
    cached,
    Cached,
    Scope,
    cached_property,
    pickled_cached_property,
    lazy_property,
)


def test_singleton():
    class SingletonClass(metaclass=Singleton):
        def __init__(self, x):
            self.x = x

    value = 4
    instance = SingletonClass(value)
    for v in (value, value + 1):
        assert SingletonClass(v) is instance


class TestCached:
    all_scopes = pytest.mark.parametrize("scope,op", [('process', operator.is_), ('thread', operator.is_not)])

    @staticmethod
    def func(x):
        return "test", x, random.random()

    def test_invalid_scope(self):
        with pytest.raises(ValueError):
            cached_call(self.func, 3.14, scope='invalid')

    @staticmethod
    def check_callable(func, op):
        assert func(5) is func(5)
        assert func(6) is not func(7)

        def run(value, out):
            out.append(func(value))

        results = []
        threads = [threading.Thread(target=run, args=(13, results)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert op(*results)

    @all_scopes
    def test_cached_function(self, scope: Scope, op):
        self.check_callable(partial(cached_call, self.func, scope=scope), op)

    @all_scopes
    def test_decorated_function(self, scope: Scope, op):
        self.check_callable(cached(scope=scope)(self.func), op)

    @all_scopes
    def test_cached_class(self, scope: Scope, op):
        class UnsuspectingClass:
            def __init__(self, x):
                self.x = x

        self.check_callable(partial(cached_call, UnsuspectingClass, scope=scope), op)

    @all_scopes
    def test_metaclass(self, scope: Scope, op):
        class CachedClass(metaclass=Cached, scope=scope):
            def __init__(self, x):
                self.x = x

        self.check_callable(CachedClass, op)


class TestCachedProperty:
    class HasCachedProperties:
        def __init__(self, x):
            self.x = x

        @cached_property
        def no_dependencies(self):
            return "no_dependencies", random.random()

        @cached_property('x')
        def depends_on_x(self):
            return f"x = {self.x}", random.random()

    def test_no_dependencies(self):
        obj = self.HasCachedProperties(3.14)
        assert obj.no_dependencies is obj.no_dependencies
        value = "new_value", random.random()
        obj.no_dependencies = value
        assert obj.no_dependencies is value
        del obj.no_dependencies
        new_value = obj.no_dependencies
        assert isinstance(new_value, tuple)
        assert new_value is not value
        assert obj.no_dependencies is new_value

    def test_dependency(self):
        obj = self.HasCachedProperties(2.718)
        value = obj.depends_on_x
        assert obj.depends_on_x is value
        assert self.HasCachedProperties.depends_on_x.is_cache_valid(obj)
        obj.x += 1
        assert not self.HasCachedProperties.depends_on_x.is_cache_valid(obj)
        assert obj.depends_on_x is not value

    def test_state(self):
        x = 4
        assert cached_property.state_without_cache(self.HasCachedProperties(x)) == {'x': x}

        class BaseWithState:
            def __init__(self, x):
                self.x = x

            def __getstate__(self):
                return self.__dict__

        class HasCP(BaseWithState):
            def __init__(self, x, y):
                super().__init__(x)
                self.y = y

            @cached_property
            def z(self):
                return random.random()

            def __getstate__(self):
                return cached_property.state_without_cache(self)

        y = 5
        assert HasCP(x, y).__getstate__() == {'x': x, 'y': y}

    @pytest.mark.timeout(5)
    def test_recursion(self):
        class RecursiveDependencies:
            @cached_property('y')
            def x(self):
                return self.y + 1

            @cached_property('x')
            def y(self):
                return self.x + 1

        rd = RecursiveDependencies()
        with pytest.raises(RecursionError):
            assert rd.x

    @pytest.mark.timeout(5)
    def test_lock_collisions(self):
        def prop_name(i):
            return f'p{i}'

        def create_cp(i):
            prev = prop_name(i - 1)

            def method(slf):
                return getattr(slf, prev, -1) + 1

            method.__name__ = prop_name(i)
            return cached_property(method, prev) if i else cached_property(method)

        n_properties = 100
        # noinspection PyPep8Naming
        Crowded = type('Crowded', (), {prop_name(i): create_cp(i) for i in range(n_properties)})
        assert getattr(Crowded(), prop_name(n_properties - 1)) == n_properties - 1


def test_pickled_cached_property(tmp_path):
    filename_template = str(tmp_path / "temp_{x}.pkl")

    class HasPickledCachedProperty:
        def __init__(self, x):
            self.x = x

        @pickled_cached_property('x', filename=filename_template)
        def prop(self):
            return self.x, random.random()

    def check_file(filename, expected_content):
        assert os.path.exists(filename)
        with open(filename, 'rb') as f:
            assert pickle.load(f, fix_imports=True, encoding='ASCII', errors='strict') == expected_content

    x = 3
    obj = HasPickledCachedProperty(x)
    value = obj.prop
    filename = filename_template.format(x=obj.x)
    check_file(filename, ((x,), value))
    assert HasPickledCachedProperty(x).prop == value

    new_value = random.random()
    obj.prop = new_value
    check_file(filename, ((x,), new_value))
    assert obj.prop == new_value

    del obj.prop
    assert not os.path.exists(filename)


def test_lazy_property():
    class HasLazyProperty:
        def __init__(self):
            self.x = None

        @lazy_property('x', 'y')
        def prop(self):
            return self.x + self.y + random.random()

    obj = HasLazyProperty()
    assert obj.prop is None
    obj.x = 2
    assert obj.prop is None
    obj.y = 3
    value = obj.prop
    assert value is not None
    assert obj.prop is value
