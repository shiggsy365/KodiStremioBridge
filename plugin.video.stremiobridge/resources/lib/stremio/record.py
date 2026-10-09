"""A lighter ``@dataclass`` for the add-on's value classes.

Every plugin call starts Python afresh. Importing dataclasses (which imports
inspect) and having it build each class cost about half of a call's start-up:
around a quarter of a second on a Fire TV Stick, for every widget and list.
This does only what the add-on uses: fields from the class's annotations, with
defaults and default factories (`field`), ``__init__`` (compiled once per
class, so making thousands of episodes stays quick), ``==``, a hash for frozen
classes, ``repr``, `replace` and `asdict`. Frozen classes aren't enforced
read-only: nothing here changes them.
"""

_MISSING = object()


class Field:
    __slots__ = ("name", "default", "default_factory", "compare", "repr")

    def __init__(self, default=_MISSING, default_factory=_MISSING, compare=True, repr=True):
        self.name = None
        self.default, self.default_factory = default, default_factory
        self.compare, self.repr = compare, repr


def field(*, default=_MISSING, default_factory=_MISSING, compare=True, repr=True):
    return Field(default, default_factory, compare, repr)


def _own_annotations(cls):
    """The class's own (Python 3.10+: never a base class's), in order."""
    return cls.__annotations__


def _build(cls, frozen):
    fields = {}
    for base in reversed(cls.__mro__[1:]):
        fields.update(getattr(base, "__record_fields__", {}))
    for name in _own_annotations(cls):
        value = cls.__dict__.get(name, _MISSING)
        spec = value if isinstance(value, Field) else Field(default=value)
        spec.name = name
        if isinstance(value, Field):
            if spec.default is _MISSING:
                delattr(cls, name)
            else:
                setattr(cls, name, spec.default)
        fields[name] = spec
    cls.__record_fields__ = fields

    if "__init__" not in cls.__dict__:
        params, body, env = [], [], {"_FACTORY": _MISSING}
        for name, spec in fields.items():
            if spec.default_factory is not _MISSING:
                env[f"_f_{name}"] = spec.default_factory
                params.append(f"{name}=_FACTORY")
                body.append(f"    self.{name} = _f_{name}() if {name} is _FACTORY else {name}")
            else:
                if spec.default is not _MISSING:
                    env[f"_d_{name}"] = spec.default
                    params.append(f"{name}=_d_{name}")
                else:
                    params.append(name)
                body.append(f"    self.{name} = {name}")
        source = f"def __init__(self, {', '.join(params)}):\n" + ("\n".join(body) or "    pass")
        exec(source, env)
        env["__init__"].__qualname__ = f"{cls.__qualname__}.__init__"
        cls.__init__ = env["__init__"]

    compared = tuple(name for name, spec in fields.items() if spec.compare)
    shown = tuple(name for name, spec in fields.items() if spec.repr)

    def key(self):
        return tuple(getattr(self, name) for name in compared)

    if "__eq__" not in cls.__dict__:
        def __eq__(self, other):
            if other.__class__ is self.__class__:
                return key(self) == key(other)
            return NotImplemented
        cls.__eq__ = __eq__
    if "__hash__" not in cls.__dict__ or cls.__dict__["__hash__"] is None:
        cls.__hash__ = (lambda self: hash(key(self))) if frozen else None
    if "__repr__" not in cls.__dict__:
        def __repr__(self):
            return f"{self.__class__.__qualname__}({', '.join(f'{n}={getattr(self, n)!r}' for n in shown)})"
        cls.__repr__ = __repr__
    return cls


def dataclass(cls=None, *, frozen=False):
    """``@dataclass`` or ``@dataclass(frozen=True)``, as the standard one."""
    if cls is None:
        return lambda c: _build(c, frozen)
    return _build(cls, frozen)


def fields_of(obj_or_cls):
    """The field names, in order."""
    return tuple(obj_or_cls.__record_fields__)


def replace(obj, **changes):
    values = {name: getattr(obj, name) for name in obj.__record_fields__}
    values.update(changes)
    return obj.__class__(**values)


def asdict(obj):
    return {name: _plain(getattr(obj, name)) for name in obj.__record_fields__}


def _plain(value):
    if hasattr(value, "__record_fields__") and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return type(value)(_plain(v) for v in value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value
