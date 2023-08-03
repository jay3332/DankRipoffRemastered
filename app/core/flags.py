from __future__ import annotations as _  # PyCharm thinking "annotations" is shadowing

import inspect
import re
import sys
from argparse import ArgumentParser as _ArgumentParser, Namespace
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Collection, Generic, Iterator, TYPE_CHECKING, Type, TypeVar

from discord.ext.commands import BadArgument, Converter, MissingRequiredArgument, run_converters
from discord.ext.commands.view import StringView
from discord.utils import MISSING, resolve_annotation

if TYPE_CHECKING:
    from app.core.models import Command, Context

    FlagMetaT = TypeVar("FlagMetaT", bound="FlagMeta")
    D = TypeVar("D")

T = TypeVar('T')


class ArgumentParser(_ArgumentParser):
    def error(self, message: str) -> None:
        raise BadArgument(message)


@dataclass
class Flag(Generic[T]):
    """Represents a flag."""
    name: str = MISSING
    dest: str = MISSING
    aliases: Collection[str] = ()
    store_true: bool = False
    converter: Converter[T] | Type[T] | None = None
    short: str | None = None
    description: str | None = None
    required: bool = False
    default: T | None = None

    def add_to(self, parser: ArgumentParser, /) -> None:
        """Adds the flag to the parser."""
        if self.name is MISSING:
            raise TypeError("name must be set.")

        if self.dest is MISSING:
            self.dest = self.name.replace('-', '_')

        args = ['--' + self.name] if self.short is None else ['--' + self.name, '-' + self.short]
        args.extend('--' + alias for alias in self.aliases)

        if not self.store_true:
            parser.add_argument(
                *args,
                nargs='+',
                dest=self.dest,
                required=self.required,
                default=None,
            )
            return

        parser.add_argument(*args, dest=self.dest, action='store_true')


def _resolve_aliases(alias: str, aliases: Collection[str]) -> list[str]:
    if alias and aliases:
        raise ValueError("alias and aliases are mutually exclusive.")

    if alias is not MISSING:
        aliases = (alias,)

    return [alias.casefold() for alias in aliases]


def flag(
    *,
    name: str = MISSING,
    short: str = None,
    alias: str = MISSING,
    aliases: Collection[str] = (),
    converter: Converter[T] | Type[T] = None,
    description: str = None,
    required: bool = False,
    default: T | None = None,
) -> Annotated[T, Flag[T]]:
    """Creates a flag dataclass."""
    return Flag(
        name=name and name.casefold(),
        short=short,
        aliases=_resolve_aliases(alias, aliases),
        converter=converter,
        description=description,
        required=required,
        default=default,
    )


def store_true(
    *,
    name: str = MISSING,
    short: str = None,
    alias: str = MISSING,
    aliases: Collection[str] = (),
    description: str = None,
) -> Annotated[bool, Flag[bool]]:
    """Creates a store true flag."""
    aliases = _resolve_aliases(alias, aliases)
    return Flag(name=name and name.casefold(), short=short, aliases=aliases, store_true=True, description=description)  # type: ignore


class FlagOrConvert(Converter[str]):
    """If this argument starts with a valid flag then stop converting."""

    def __init__(self) -> None:
        self._err: BaseException | None = None

    async def convert(self, ctx: Context, argument: str) -> str:
        if self._err is not None:
            raise StopIteration

        if not isinstance(ctx.command, Command) or ctx.command.custom_flags is None:
            raise TypeError

        if ctx.command.custom_flags.is_flag_starter(argument):
            raise StopIteration

        return argument


class ConsumeUntilFlag(Converter[T]):
    def __init__(self, converter: Converter[T] | Type[T], default: T = MISSING) -> None:
        self.converter: Converter[T] | Type[T] = converter
        self.default: T = default

    async def convert(self, ctx: Context, argument: str) -> T:
        from app.core.models import Command

        if not isinstance(ctx.command, Command) or ctx.command.custom_flags is None:
            raise TypeError

        if ctx.command.custom_flags.is_flag_starter(argument):
            if self.default is not MISSING:
                return self.default

            raise MissingRequiredArgument(ctx.current_parameter)

        ctx.view.undo()
        rest = ctx.view.read_rest()
        parts = Flags.WS_SPLIT_REGEX.split(rest)

        valid = []
        for part in parts:
            if not part:
                continue

            if ctx.command.custom_flags.is_flag_starter(part):
                break

            valid.append(part)

        argument = ''.join(valid).strip()
        ctx.view.index = ctx.view.buffer.rfind(argument) + len(argument)

        if not self.converter:
            return argument

        return await run_converters(ctx, self.converter, argument, ctx.current_parameter)


def _get_namespaces(attrs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        global_ns = sys.modules[attrs['__module__']].__dict__
    except KeyError:
        global_ns = {}

    frame = inspect.currentframe()
    try:
        if frame is None:
            local_ns = {}
        else:
            parent = frame if frame.f_back is None else frame.f_back
            local_ns = parent.f_locals
    finally:
        del frame

    return local_ns, global_ns


# noinspection PyShadowingNames
def _resolve_flag_annotation(flag: Flag[Any], annotation: Any, *args: Any) -> None:
    annotation = resolve_annotation(annotation, *args)
    if annotation is type(None) or not annotation:
        annotation = str

    flag.converter = annotation


def _resolve_flags(attrs: dict[str, T]) -> dict[str, Flag[T]]:
    local_ns, global_ns = _get_namespaces(attrs)
    annotations = attrs.get('__annotations__', {})

    flags = {}
    args = global_ns, local_ns, {}

    for name, value in attrs.items():
        if name.startswith('__') or not isinstance(value, Flag):
            continue

        if value.converter is None and not value.store_true:
            _resolve_flag_annotation(value, annotations[name], *args)

        value.dest = name = name.casefold()
        if value.name is MISSING:
            value.name = name

        flags[name] = value

    for name, annotation in annotations.items():
        if name in flags:
            continue

        flags[name] = res = flag(name=name.casefold())
        res.dest = name

        _resolve_flag_annotation(res, annotation, *args)

    return flags


class FlagMeta(type, Generic[T]):
    if TYPE_CHECKING:
        _flags: dict[str, Flag[T]]
        _parser: ArgumentParser

    def __new__(mcs: Type[FlagMetaT], name: str, bases: tuple[Type[Any], ...], attrs: dict[str, Any]) -> FlagMetaT:
        cls = super().__new__(mcs, name, bases, attrs)
        cls.__doc__ = inspect.cleandoc(inspect.getdoc(cls))

        cls._flags = flags = _resolve_flags(attrs)
        cls._parser = parser = ArgumentParser(description=cls.__doc__)

        # noinspection PyShadowingNames
        for flag in flags.values():
            flag.add_to(parser)

        return cls

    @property
    def flags(cls) -> dict[str, Flag[T]]:
        return cls._flags

    @property
    def parser(cls) -> ArgumentParser:
        return cls._parser

    @property
    def default(cls) -> FlagNamespace[T]:
        """Returns a Namespace with all flags set to their default values or ``None``.

        If a flag is required then this will raise a ValueError.
        """
        # noinspection PyShadowingNames
        if any(flag.required for flag in cls.flags.values()):
            raise ValueError('cannot set as default')

        kwargs = {v.dest: False if v.store_true else v.default for v in cls.flags.values()}

        ns = Namespace(**kwargs)
        return FlagNamespace(ns, cls)

    def get_flag(cls, name: str) -> Flag[T]:
        return cls._flags[name.casefold()]

    def is_flag_starter(cls, sample: str) -> bool:
        """Return whether the sample starts with a valid flag."""
        sample, *_ = sample.lstrip().split(' ', maxsplit=1)
        sample, _, _ = sample.replace('\u2014', '--').partition('=')

        if not sample.startswith('-'):
            return False

        # noinspection PyShadowingNames
        for flag in cls.walk_flags():
            if flag.short and sample == '-' + flag.short:
                return True

            if flag.name and sample.casefold() == '--' + flag.name:
                return True

            if any(sample.casefold() == '--' + alias for alias in flag.aliases):
                return True

        for part in sample.split():
            # Check for combined short flag syntax, e.g. -a -b can become -ab
            if part.startswith('--') or not part.startswith('-'):
                continue

            if all(any(subject == f.short for f in cls.walk_flags()) for subject in part[1:]):
                return True

        return False

    def walk_flags(cls) -> Iterator[Flag[T]]:
        yield from cls._flags.values()

    def inject(cls, command: Command) -> None:
        command.custom_flags = cls._flags


class FlagNamespace(Generic[T]):
    """Represents a namespace of flags."""

    if TYPE_CHECKING:
        __argparse_namespace__: Namespace
        __flags__: FlagMeta

    def __init__(self, ns: Namespace, flags: FlagMeta) -> None:
        self.__argparse_namespace__ = ns
        self.__flags__ = flags

    def __getattr__(self, item: str) -> T:
        return getattr(self.__argparse_namespace__, item)

    def get(self, item: str, default: D = None) -> T | D:
        try:
            return getattr(self, item)
        except AttributeError:
            return default

    __getitem__ = __getattr__

    def __contains__(self, item: str) -> bool:
        return item in self.__argparse_namespace__

    def __iter__(self) -> Iterator[tuple[str, T]]:
        yield from self.__argparse_namespace__.__dict__.items()

    def __repr__(self) -> str:
        return repr(self.__argparse_namespace__)

    def __len__(self) -> int:
        return sum(1 for _ in self)


class _FakeIdentifier(str):
    def isidentifier(self) -> bool:
        return True


class Flags(metaclass=FlagMeta):  # type: FlagMeta[T]
    """Base class for all flag groups."""

    WS_SPLIT_REGEX: ClassVar[re.Pattern[str]] = re.compile(r'(\s+\S+)')

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> FlagNamespace[T]:
        # sourcery no-metrics
        try:
            flags: FlagMeta[T] = ctx.command.custom_flags
        except Exception as exc:
            raise TypeError(f'bad flag annotation: {exc}')

        parts = cls.WS_SPLIT_REGEX.split(argument)
        buffer = []
        args = []

        for part in parts:
            if not part:
                continue

            if part.isspace():
                buffer.append(part)
                continue

            if flags.is_flag_starter(part):
                if joined := ''.join(buffer):
                    args.append(joined)

                args.append(part.lstrip().replace('\u2014', '--'))
                buffer = []
                continue

            buffer.append(part)

        if joined := ''.join(buffer):
            args.append(joined)

        ns = flags.parser.parse_args(args)
        for k, v in ns.__dict__.items():
            # noinspection PyShadowingNames
            flag = flags.get_flag(k)
            if isinstance(v, list):
                v = ''.join(v)

            if isinstance(v, str):  # will also trigger if v was originally a list, intentional.
                v = v.strip()

            converter = flag.converter
            if converter and v is not None:
                param = ctx.current_parameter.replace(name=_FakeIdentifier(f'{ctx.current_parameter.name}.{k}'))

                is_list = False
                try:
                    origin = converter.__origin__
                    args = converter.__args__
                except AttributeError:
                    pass
                else:
                    if origin is list:
                        is_list = True

                if is_list:
                    converter = args[0]
                    view = StringView(v)
                    v = []

                    while not view.eof:
                        view.skip_ws()

                        if view.eof:
                            break

                        word = view.get_quoted_word()
                        v.append(await run_converters(ctx, converter, word, param))
                else:
                    v = await run_converters(ctx, converter, v, param)

            elif v is None and flag.required:
                # we should never actually get here because of argparse.
                raise BadArgument(f'flag {flag.name!r} is required.')

            if v is None:
                v = flag.default
            setattr(ns, k, v)

        return FlagNamespace(ns, cls)
