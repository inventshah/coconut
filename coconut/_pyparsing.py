#!/usr/bin/env python
# -*- coding: utf-8 -*-

# -----------------------------------------------------------------------------------------------------------------------
# INFO:
# -----------------------------------------------------------------------------------------------------------------------

"""
Author: Evan Hubinger
License: Apache 2.0
Description: Wrapper around PyParsing that selects the best available implementation.
"""

# -----------------------------------------------------------------------------------------------------------------------
# IMPORTS:
# -----------------------------------------------------------------------------------------------------------------------

from __future__ import print_function, absolute_import, unicode_literals, division

from coconut.root import *  # NOQA

import os
import re
import sys
import traceback
import functools
import inspect
from warnings import warn
from collections import defaultdict

from coconut.constants import (
    PURE_PYTHON,
    use_fast_pyparsing_reprs,
    use_packrat_parser,
    packrat_cache_size,
    default_whitespace_chars,
    varchars,
    min_versions,
    pure_python_env_var,
    enable_pyparsing_warnings,
    use_left_recursion_if_available,
    get_bool_env_var,
    use_computation_graph_env_var,
    use_incremental_if_available,
    default_incremental_cache_size,
    never_clear_incremental_cache,
    warn_on_multiline_regex,
)
from coconut.util import get_clock_time  # NOQA
from coconut.util import (
    ver_str_to_tuple,
    ver_tuple_to_str,
    get_next_version,
)

# warning: do not name this file cPyparsing or pyparsing or it might collide with the following imports
try:

    if PURE_PYTHON:
        raise ImportError("skipping cPyparsing check due to " + pure_python_env_var + " = " + os.getenv(pure_python_env_var, ""))

    import cPyparsing as _pyparsing
    from cPyparsing import *  # NOQA
    from cPyparsing import __version__

    PYPARSING_PACKAGE = "cPyparsing"
    PYPARSING_INFO = "Cython cPyparsing v" + __version__

except ImportError:
    try:

        import pyparsing as _pyparsing
        from pyparsing import *  # NOQA
        from pyparsing import __version__

        PYPARSING_PACKAGE = "pyparsing"
        PYPARSING_INFO = "Python pyparsing v" + __version__

    except ImportError:
        traceback.print_exc()
        __version__ = None
        PYPARSING_PACKAGE = "cPyparsing"
        PYPARSING_INFO = None


# -----------------------------------------------------------------------------------------------------------------------
# VERSIONING:
# -----------------------------------------------------------------------------------------------------------------------

min_ver = min(min_versions["pyparsing"], min_versions["cPyparsing"][:3])  # inclusive
max_ver = get_next_version(max(min_versions["pyparsing"], min_versions["cPyparsing"][:3]))  # exclusive
cur_ver = None if __version__ is None else ver_str_to_tuple(__version__)

min_ver_str = ver_tuple_to_str(min_ver)
max_ver_str = ver_tuple_to_str(max_ver)

if cur_ver is None or cur_ver < min_ver:
    raise ImportError(
        "This version of Coconut requires pyparsing/cPyparsing version >= " + min_ver_str
        + ("; got " + PYPARSING_INFO if PYPARSING_INFO is not None else "")
        + " (run '{python} -m pip install --upgrade {package}' to fix)".format(python=sys.executable, package=PYPARSING_PACKAGE),
    )
elif cur_ver >= max_ver:
    warn(
        "This version of Coconut was built for pyparsing/cPyparsing versions < " + max_ver_str
        + ("; got " + PYPARSING_INFO if PYPARSING_INFO is not None else "")
        + " (run '{python} -m pip install {package}<{max_ver}' to fix)".format(python=sys.executable, package=PYPARSING_PACKAGE, max_ver=max_ver_str),
    )

MODERN_PYPARSING = cur_ver >= (3,)

if MODERN_PYPARSING:
    warn(
        "This version of Coconut is not built for pyparsing v3; some syntax features WILL NOT WORK"
        + " (run either '{python} -m pip install cPyparsing<{max_ver}' or '{python} -m pip install pyparsing<{max_ver}' to fix)".format(python=sys.executable, max_ver=max_ver_str),
    )


# -----------------------------------------------------------------------------------------------------------------------
# OVERRIDES:
# -----------------------------------------------------------------------------------------------------------------------

if PYPARSING_PACKAGE != "cPyparsing":
    if not MODERN_PYPARSING:
        HIT, MISS = 0, 1

        def _parseCache(self, instring, loc, doActions=True, callPreParse=True):
            # [CPYPARSING] include packrat_context
            lookup = (self, instring, loc, callPreParse, doActions, tuple(self.packrat_context))
            with ParserElement.packrat_cache_lock:
                cache = ParserElement.packrat_cache
                value = cache.get(lookup)
                if value is cache.not_in_cache:
                    ParserElement.packrat_cache_stats[MISS] += 1
                    try:
                        value = self._parseNoCache(instring, loc, doActions, callPreParse)
                    except ParseBaseException as pe:
                        # cache a copy of the exception, without the traceback
                        cache.set(lookup, pe.__class__(*pe.args))
                        raise
                    else:
                        cache.set(lookup, (value[0], value[1].copy()))
                        return value
                else:
                    ParserElement.packrat_cache_stats[HIT] += 1
                    if isinstance(value, Exception):
                        raise value
                    return value[0], value[1].copy()
        ParserElement.packrat_context = []
        ParserElement._parseCache = _parseCache

elif not hasattr(ParserElement, "packrat_context"):
    raise ImportError(
        "This version of Coconut requires cPyparsing>=" + ver_tuple_to_str(min_versions["cPyparsing"])
        + "; got cPyparsing==" + __version__
        + " (run '{python} -m pip install --upgrade cPyparsing' to fix)".format(python=sys.executable),
    )

if hasattr(ParserElement, "enableIncremental"):
    SUPPORTS_INCREMENTAL = sys.version_info >= (3, 8)  # avoids stack overflows on py<=37
else:
    SUPPORTS_INCREMENTAL = False
    ParserElement._incrementalEnabled = False
    ParserElement._incrementalWithResets = False

    def enableIncremental(*args, **kwargs):
        """Dummy version of enableIncremental that just raises an error."""
        raise ImportError(
            "incremental parsing only supported on cPyparsing>="
            + ver_tuple_to_str(min_versions["cPyparsing"])
            + " (run '{python} -m pip install --upgrade cPyparsing' to fix)".format(python=sys.executable)
        )


# -----------------------------------------------------------------------------------------------------------------------
# SETUP:
# -----------------------------------------------------------------------------------------------------------------------

if MODERN_PYPARSING:
    _trim_arity = _pyparsing.core._trim_arity
    _ParseResultsWithOffset = _pyparsing.core._ParseResultsWithOffset
else:
    _trim_arity = _pyparsing._trim_arity
    _ParseResultsWithOffset = _pyparsing._ParseResultsWithOffset

USE_COMPUTATION_GRAPH = get_bool_env_var(
    use_computation_graph_env_var,
    default=(
        not MODERN_PYPARSING  # not yet supported
        # commented out to minimize memory footprint when running tests:
        # and not PYPY  # experimentally determined
    ),
)

if enable_pyparsing_warnings:
    if MODERN_PYPARSING:
        _pyparsing.enable_all_warnings()
    else:
        _pyparsing._enable_all_warnings()
    _pyparsing.__diag__.warn_name_set_on_empty_Forward = False
    _pyparsing.__diag__.warn_on_incremental_multiline_regex = warn_on_multiline_regex

if MODERN_PYPARSING and use_left_recursion_if_available:
    ParserElement.enable_left_recursion()
elif SUPPORTS_INCREMENTAL and use_incremental_if_available:
    ParserElement.enableIncremental(default_incremental_cache_size, still_reset_cache=not never_clear_incremental_cache)
elif use_packrat_parser:
    ParserElement.enablePackrat(packrat_cache_size)

ParserElement.setDefaultWhitespaceChars(default_whitespace_chars)

Keyword.setDefaultKeywordChars(varchars)

if SUPPORTS_INCREMENTAL:
    all_parse_elements = ParserElement.collectParseElements()
else:
    all_parse_elements = None


# -----------------------------------------------------------------------------------------------------------------------
# MISSING OBJECTS:
# -----------------------------------------------------------------------------------------------------------------------

python_quoted_string = getattr(_pyparsing, "python_quoted_string", None)
if python_quoted_string is None:
    python_quoted_string = _pyparsing.Combine(
        (_pyparsing.Regex(r'"""(?:[^"\\]|""(?!")|"(?!"")|\\.)*', flags=re.MULTILINE) + '"""').setName("multiline double quoted string")
        | (_pyparsing.Regex(r"'''(?:[^'\\]|''(?!')|'(?!'')|\\.)*", flags=re.MULTILINE) + "'''").setName("multiline single quoted string")
        | (_pyparsing.Regex(r'"(?:[^"\n\r\\]|(?:\\")|(?:\\(?:[^x]|x[0-9a-fA-F]+)))*') + '"').setName("double quoted string")
        | (_pyparsing.Regex(r"'(?:[^'\n\r\\]|(?:\\')|(?:\\(?:[^x]|x[0-9a-fA-F]+)))*") + "'").setName("single quoted string")
    ).setName("Python quoted string")
    _pyparsing.python_quoted_string = python_quoted_string


# -----------------------------------------------------------------------------------------------------------------------
# FAST REPRS:
# -----------------------------------------------------------------------------------------------------------------------

if PY2:
    def fast_repr(cls):
        """A very simple, fast __repr__/__str__ implementation."""
        return "<" + cls.__name__ + ">"
else:
    fast_repr = object.__repr__


_old_pyparsing_reprs = []


def set_fast_pyparsing_reprs():
    """Make pyparsing much faster by preventing it from computing expensive nested string representations."""
    for obj in vars(_pyparsing).values():
        try:
            if issubclass(obj, ParserElement):
                _old_pyparsing_reprs.append((obj, (obj.__repr__, obj.__str__)))
                obj.__repr__ = functools.partial(fast_repr, obj)
                obj.__str__ = functools.partial(fast_repr, obj)
        except TypeError:
            pass


def unset_fast_pyparsing_reprs():
    """Restore pyparsing's default string representations for ease of debugging."""
    for obj, (repr_method, str_method) in _old_pyparsing_reprs:
        obj.__repr__ = repr_method
        obj.__str__ = str_method
    _old_pyparsing_reprs[:] = []


if use_fast_pyparsing_reprs:
    set_fast_pyparsing_reprs()


# -----------------------------------------------------------------------------------------------------------------------
# PROFILING:
# -----------------------------------------------------------------------------------------------------------------------

_timing_info = [None]  # in list to allow reassignment


class _timing_sentinel(object):
    __slots__ = ()


def add_timing_to_method(cls, method_name, method):
    """Add timing collection to the given method.
    It's a monstrosity, but it's only used for profiling."""
    from coconut.terminal import internal_assert  # hide to avoid circular import

    if hasattr(inspect, "getargspec"):
        args, varargs, varkw, defaults = inspect.getargspec(method)
    else:
        args, varargs, varkw, defaults, kwonlyargs, kwonlydefaults, annotations = inspect.getfullargspec(method)
    internal_assert(args[:1] == ["self"], "cannot add timing to method", method_name)

    if not defaults:
        defaults = []
    num_undefaulted_args = len(args) - len(defaults)
    def_args = []
    call_args = []
    fix_arg_defaults = []
    defaults_dict = {}
    for i, arg in enumerate(args):
        if i >= num_undefaulted_args:
            default = defaults[i - num_undefaulted_args]
            def_args.append(arg + "=_timing_sentinel")
            defaults_dict[arg] = default
            fix_arg_defaults.append(
                """
    if {arg} is _timing_sentinel:
        {arg} = _exec_dict["defaults_dict"]["{arg}"]
""".strip("\n").format(
                    arg=arg,
                ),
            )
        else:
            def_args.append(arg)
        call_args.append(arg)
    if varargs:
        def_args.append("*" + varargs)
        call_args.append("*" + varargs)
    if varkw:
        def_args.append("**" + varkw)
        call_args.append("**" + varkw)

    new_method_name = "new_" + method_name + "_func"
    _exec_dict = globals().copy()
    _exec_dict.update(locals())
    new_method_code = """
def {new_method_name}({def_args}):
{fix_arg_defaults}

    _all_args = (lambda *args, **kwargs: args + tuple(kwargs.values()))({call_args})
    _exec_dict["internal_assert"](not any(_arg is _timing_sentinel for _arg in _all_args), "error handling arguments in timed method {new_method_name}({def_args}); got", _all_args)

    _start_time = _exec_dict["get_clock_time"]()
    try:
        return _exec_dict["method"]({call_args})
    finally:
        _timing_info[0][str(self)] += _exec_dict["get_clock_time"]() - _start_time
{new_method_name}._timed = True
    """.format(
        fix_arg_defaults="\n".join(fix_arg_defaults),
        new_method_name=new_method_name,
        def_args=", ".join(def_args),
        call_args=", ".join(call_args),
    )
    exec(new_method_code, _exec_dict)

    setattr(cls, method_name, _exec_dict[new_method_name])
    return True


def collect_timing_info():
    """Modifies pyparsing elements to time how long they're executed for.
    It's a monstrosity, but it's only used for profiling."""
    from coconut.terminal import logger  # hide to avoid circular imports
    logger.log("adding timing to pyparsing elements:")
    _timing_info[0] = defaultdict(float)
    for obj in vars(_pyparsing).values():
        if isinstance(obj, type) and issubclass(obj, ParserElement):
            added_timing = False
            for attr_name in dir(obj):
                attr = getattr(obj, attr_name)
                if (
                    callable(attr)
                    and not isinstance(attr, ParserElement)
                    and not getattr(attr, "_timed", False)
                    and attr_name not in (
                        "__getattribute__",
                        "__setattribute__",
                        "__init_subclass__",
                        "__subclasshook__",
                        "__class__",
                        "__setattr__",
                        "__getattr__",
                        "__new__",
                        "__init__",
                        "__str__",
                        "__repr__",
                        "__hash__",
                        "__eq__",
                        "_trim_traceback",
                        "_ErrorStop",
                        "_UnboundedCache",
                        "enablePackrat",
                        "enableIncremental",
                        "inlineLiteralsUsing",
                        "setDefaultWhitespaceChars",
                        "setDefaultKeywordChars",
                        "resetCache",
                    )
                ):
                    added_timing |= add_timing_to_method(obj, attr_name, attr)
            if added_timing:
                logger.log("\tadded timing to", obj)
    return _timing_info


def print_timing_info():
    """Print timing_info collected by collect_timing_info()."""
    print(
        """
=====================================
Timing info:
(timed {num} total pyparsing objects)
=====================================
        """.rstrip().format(
            num=len(_timing_info[0]),
        ),
    )
    sorted_timing_info = sorted(_timing_info[0].items(), key=lambda kv: kv[1])
    for method_name, total_time in sorted_timing_info:
        print("{method_name}:\t{total_time}".format(method_name=method_name, total_time=total_time))


_profiled_MatchFirst_objs = {}


def add_profiling_to_MatchFirsts():
    """Add profiling to MatchFirst objects to look for possible reorderings."""

    def new_parseImpl(self, instring, loc, doActions=True):
        if id(self) not in _profiled_MatchFirst_objs:
            _profiled_MatchFirst_objs[id(self)] = self
            self.expr_usage_stats = [0] * len(self.exprs)
            self.expr_timing_stats = [[] for _ in range(len(self.exprs))]
        maxExcLoc = -1
        maxException = None
        for i, e in enumerate(self.exprs):
            try:
                start_time = get_clock_time()
                try:
                    ret = e._parse(instring, loc, doActions)
                finally:
                    self.expr_timing_stats[i].append(get_clock_time() - start_time)
                self.expr_usage_stats[i] += 1
                return ret
            except _pyparsing.ParseException as err:
                if err.loc > maxExcLoc:
                    maxException = err
                    maxExcLoc = err.loc
            except IndexError:
                if len(instring) > maxExcLoc:
                    maxException = _pyparsing.ParseException(instring, len(instring), e.errmsg, self)
                    maxExcLoc = len(instring)
        else:
            if maxException is not None:
                maxException.msg = self.errmsg
                raise maxException
            else:
                raise _pyparsing.ParseException(instring, loc, "no defined alternatives to match", self)
    _pyparsing.MatchFirst.parseImpl = new_parseImpl
    return _profiled_MatchFirst_objs


def time_for_ordering(expr_usage_stats, expr_timing_aves):
    """Get the total time for a given MatchFirst ordering."""
    total_time = 0
    for i, n in enumerate(expr_usage_stats):
        total_time += n * sum(expr_timing_aves[:i + 1])
    return total_time


def naive_timing_improvement(expr_usage_stats, expr_timing_aves):
    """Get the expected timing improvement for a better MatchFirst ordering."""
    usage_ordered_expr_usage_stats, usage_ordered_expr_timing_aves = zip(*sorted(
        zip(expr_usage_stats, expr_timing_aves),
        reverse=True,
    ))
    return time_for_ordering(usage_ordered_expr_usage_stats, usage_ordered_expr_timing_aves) - time_for_ordering(expr_usage_stats, expr_timing_aves)


def print_poorly_ordered_MatchFirsts():
    """Print poorly ordered MatchFirsts."""
    for obj in _profiled_MatchFirst_objs.values():
        obj.expr_timing_aves = [sum(ts) / len(ts) if ts else 0 for ts in obj.expr_timing_stats]
        obj.naive_timing_improvement = naive_timing_improvement(obj.expr_usage_stats, obj.expr_timing_aves)
    most_improveable = sorted(_profiled_MatchFirst_objs.values(), key=lambda obj: obj.naive_timing_improvement)[-100:]
    for obj in most_improveable:
        print(obj, ":", obj.naive_timing_improvement)
        print("\t" + repr(obj.expr_usage_stats))
        print("\t" + repr(obj.expr_timing_aves))


def start_profiling():
    """Do all the setup to begin profiling."""
    collect_timing_info()
    add_profiling_to_MatchFirsts()


def print_profiling_results():
    """Print all profiling results."""
    print_timing_info()
    print_poorly_ordered_MatchFirsts()
