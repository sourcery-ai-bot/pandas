""" manage PyTables query interface via Expressions """

import ast
import time
import warnings
from functools import partial
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from pandas.compat import u, string_types, PY3, DeepChainMap
from pandas.core.base import StringMixin
import pandas.core.common as com
from pandas.computation import expr, ops
from pandas.computation.ops import is_term, UndefinedVariableError
from pandas.computation.scope import _ensure_scope
from pandas.computation.expr import BaseExprVisitor
from pandas.computation.common import _ensure_decoded
from pandas.tseries.timedeltas import _coerce_scalar_to_timedelta_type


class Scope(expr.Scope):
    __slots__ = 'queryables',

    def __init__(self, level, global_dict=None, local_dict=None,
                 queryables=None):
        super(Scope, self).__init__(level + 1, global_dict=global_dict,
                                    local_dict=local_dict)
        self.queryables = queryables or dict()


class Term(ops.Term):

    def __new__(cls, name, env, side=None, encoding=None):
        klass = Constant if not isinstance(name, string_types) else cls
        supr_new = StringMixin.__new__
        return supr_new(klass)

    def __init__(self, name, env, side=None, encoding=None):
        super(Term, self).__init__(name, env, side=side, encoding=encoding)

    def _resolve_name(self):
        # must be a queryables
        if self.side == 'left':
            if self.name not in self.env.queryables:
                raise NameError('name {0!r} is not defined'.format(self.name))
            return self.name

        # resolve the rhs (and allow it to be None)
        try:
            return self.env.resolve(self.name, is_local=False)
        except UndefinedVariableError:
            return self.name

    @property
    def value(self):
        return self._value


class Constant(Term):

    def __init__(self, value, env, side=None, encoding=None):
        super(Constant, self).__init__(value, env, side=side,
                                       encoding=encoding)

    def _resolve_name(self):
        return self._name


class BinOp(ops.BinOp):

    _max_selectors = 31

    def __init__(self, op, lhs, rhs, queryables, encoding):
        super(BinOp, self).__init__(op, lhs, rhs)
        self.queryables = queryables
        self.encoding = encoding
        self.filter = None
        self.condition = None

    def _disallow_scalar_only_bool_ops(self):
        pass

    def prune(self, klass):

        def pr(left, right):
            """ create and return a new specialized BinOp from myself """

            if left is None:
                return right
            elif right is None:
                return left

            k = klass
            if isinstance(left, ConditionBinOp):
                if (isinstance(left, ConditionBinOp) and
                        isinstance(right, ConditionBinOp)):
                    k = JointConditionBinOp
                elif isinstance(left, k):
                    return left
                elif isinstance(right, k):
                    return right

            elif isinstance(left, FilterBinOp):
                if (isinstance(left, FilterBinOp) and
                        isinstance(right, FilterBinOp)):
                    k = JointFilterBinOp
                elif isinstance(left, k):
                    return left
                elif isinstance(right, k):
                    return right

            return k(self.op, left, right, queryables=self.queryables,
                     encoding=self.encoding).evaluate()

        left, right = self.lhs, self.rhs

        if is_term(left) and is_term(right):
            res = pr(left.value, right.value)
        elif not is_term(left) and is_term(right):
            res = pr(left.prune(klass), right.value)
        elif is_term(left) and not is_term(right):
            res = pr(left.value, right.prune(klass))
        elif not (is_term(left) or is_term(right)):
            res = pr(left.prune(klass), right.prune(klass))

        return res

    def conform(self, rhs):
        """ inplace conform rhs """
        if not com.is_list_like(rhs):
            rhs = [rhs]
        if hasattr(self.rhs, 'ravel'):
            rhs = rhs.ravel()
        return rhs

    @property
    def is_valid(self):
        """ return True if this is a valid field """
        return self.lhs in self.queryables

    @property
    def is_in_table(self):
        """ return True if this is a valid column name for generation (e.g. an
        actual column in the table) """
        return self.queryables.get(self.lhs) is not None

    @property
    def kind(self):
        """ the kind of my field """
        return getattr(self.queryables.get(self.lhs),'kind',None)

    @property
    def meta(self):
        """ the meta of my field """
        return getattr(self.queryables.get(self.lhs),'meta',None)

    @property
    def metadata(self):
        """ the metadata of my field """
        return getattr(self.queryables.get(self.lhs),'metadata',None)

    def generate(self, v):
        """ create and return the op string for this TermValue """
        val = v.tostring(self.encoding)
        return f"({self.lhs} {self.op} {val})"

    def convert_value(self, v):
        """ convert the expression that is in the term to something that is
        accepted by pytables """

        def stringify(value):
            if self.encoding is not None:
                encoder = partial(com.pprint_thing_encoded,
                                  encoding=self.encoding)
            else:
                encoder = com.pprint_thing
            return encoder(value)

        kind = _ensure_decoded(self.kind)
        meta = _ensure_decoded(self.meta)
        if kind in [u('datetime64'), u('datetime')]:
            if isinstance(v, (int, float)):
                v = stringify(v)
            v = _ensure_decoded(v)
            v = pd.Timestamp(v)
            if v.tz is not None:
                v = v.tz_convert('UTC')
            return TermValue(v, v.value, kind)
        elif (isinstance(v, datetime) or hasattr(v, 'timetuple') or
                kind == u('date')):
            v = time.mktime(v.timetuple())
            return TermValue(v, pd.Timestamp(v), kind)
        elif kind in [u('timedelta64'), u('timedelta')]:
            v = _coerce_scalar_to_timedelta_type(v, unit='s').value
            return TermValue(int(v), v, kind)
        elif meta == u('category'):
            metadata = com._values_from_object(self.metadata)
            result = metadata.searchsorted(v,side='left')
            return TermValue(result, result, u('integer'))
        elif kind == u('integer'):
            v = int(float(v))
            return TermValue(v, v, kind)
        elif kind == u('float'):
            v = float(v)
            return TermValue(v, v, kind)
        elif kind == u('bool'):
            if isinstance(v, string_types):
                v = v.strip().lower() not in [
                    u('false'),
                    u('f'),
                    u('no'),
                    u('n'),
                    u('none'),
                    u('0'),
                    u('[]'),
                    u('{}'),
                    u(''),
                ]
            else:
                v = bool(v)
            return TermValue(v, v, kind)
        elif not isinstance(v, string_types):
            v = stringify(v)
            return TermValue(v, stringify(v), u('string'))

        # string quoting
        return TermValue(v, stringify(v), u('string'))

    def convert_values(self):
        pass


class FilterBinOp(BinOp):

    def __unicode__(self):
        return com.pprint_thing("[Filter : [{0}] -> "
                                "[{1}]".format(self.filter[0], self.filter[1]))

    def invert(self):
        """ invert the filter """
        if self.filter is not None:
            f = list(self.filter)
            f[1] = self.generate_filter_op(invert=True)
            self.filter = tuple(f)
        return self

    def format(self):
        """ return the actual filter format """
        return [self.filter]

    def evaluate(self):

        if not self.is_valid:
            raise ValueError(f"query term is not valid [{self}]")

        rhs = self.conform(self.rhs)
        values = [TermValue(v, v, self.kind) for v in rhs]

        if self.is_in_table:

            # if too many values to create the expression, use a filter instead
            if self.op in ['==', '!='] and len(values) > self._max_selectors:

                filter_op = self.generate_filter_op()
                self.filter = (
                    self.lhs,
                    filter_op,
                    pd.Index([v.value for v in values]))

                return self
            return None

        # equality conditions
        if self.op in ['==', '!=']:

            filter_op = self.generate_filter_op()
            self.filter = (
                self.lhs,
                filter_op,
                pd.Index([v.value for v in values]))

        else:
            raise TypeError(
                f"passing a filterable condition to a non-table indexer [{self}]"
            )

        return self

    def generate_filter_op(self, invert=False):
        if (self.op == '!=' and not invert) or (self.op == '==' and invert):
            return lambda axis, vals: ~axis.isin(vals)
        else:
            return lambda axis, vals: axis.isin(vals)


class JointFilterBinOp(FilterBinOp):

    def format(self):
        raise NotImplementedError("unable to collapse Joint Filters")

    def evaluate(self):
        return self


class ConditionBinOp(BinOp):

    def __unicode__(self):
        return com.pprint_thing("[Condition : [{0}]]".format(self.condition))

    def invert(self):
        """ invert the condition """
        # if self.condition is not None:
        #    self.condition = "~(%s)" % self.condition
        # return self
        raise NotImplementedError("cannot use an invert condition when "
                                  "passing to numexpr")

    def format(self):
        """ return the actual ne format """
        return self.condition

    def evaluate(self):

        if not self.is_valid:
            raise ValueError(f"query term is not valid [{self}]")

        # convert values if we are in the table
        if not self.is_in_table:
            return None

        rhs = self.conform(self.rhs)
        values = [self.convert_value(v) for v in rhs]

        # equality conditions
        if self.op in ['==', '!=']:

            if len(values) > self._max_selectors:
                return None
            vs = [self.generate(v) for v in values]
            self.condition = f"({' | '.join(vs)})"

        else:
            self.condition = self.generate(values[0])

        return self


class JointConditionBinOp(ConditionBinOp):

    def evaluate(self):
        self.condition = f"({self.lhs.condition} {self.op} {self.rhs.condition})"
        return self


class UnaryOp(ops.UnaryOp):

    def prune(self, klass):

        if self.op != '~':
            raise NotImplementedError("UnaryOp only support invert type ops")

        operand = self.operand
        operand = operand.prune(klass)

        if operand is not None:
            if (
                issubclass(klass, ConditionBinOp)
                and operand.condition is not None
                or not issubclass(klass, ConditionBinOp)
                and issubclass(klass, FilterBinOp)
                and operand.filter is not None
            ):
                return operand.invert()
        return None


_op_classes = {'unary': UnaryOp}


class ExprVisitor(BaseExprVisitor):
    const_type = Constant
    term_type = Term

    def __init__(self, env, engine, parser, **kwargs):
        super(ExprVisitor, self).__init__(env, engine, parser)
        for bin_op in self.binary_ops:
            setattr(self, 'visit_{0}'.format(self.binary_op_nodes_map[bin_op]),
                    lambda node, bin_op=bin_op: partial(BinOp, bin_op,
                                                        **kwargs))

    def visit_UnaryOp(self, node, **kwargs):
        if isinstance(node.op, (ast.Not, ast.Invert)):
            return UnaryOp('~', self.visit(node.operand))
        elif isinstance(node.op, ast.USub):
            return self.const_type(-self.visit(node.operand).value, self.env)
        elif isinstance(node.op, ast.UAdd):
            raise NotImplementedError('Unary addition not supported')

    def visit_Index(self, node, **kwargs):
        return self.visit(node.value).value

    def visit_Assign(self, node, **kwargs):
        cmpr = ast.Compare(ops=[ast.Eq()], left=node.targets[0],
                           comparators=[node.value])
        return self.visit(cmpr)

    def visit_Subscript(self, node, **kwargs):
        # only allow simple suscripts

        value = self.visit(node.value)
        slobj = self.visit(node.slice)
        try:
            value = value.value
        except:
            pass

        try:
            return self.const_type(value[slobj], self.env)
        except TypeError:
            raise ValueError("cannot subscript {0!r} with "
                             "{1!r}".format(value, slobj))

    def visit_Attribute(self, node, **kwargs):
        attr = node.attr
        value = node.value

        ctx = node.ctx.__class__
        if ctx == ast.Load:
            # resolve the value
            resolved = self.visit(value)

            # try to get the value to see if we are another expression
            try:
                resolved = resolved.value
            except (AttributeError):
                pass

            try:
                return self.term_type(getattr(resolved, attr), self.env)
            except AttributeError:

                # something like datetime.datetime where scope is overriden
                if isinstance(value, ast.Name) and value.id == attr:
                    return resolved

        raise ValueError("Invalid Attribute context {0}".format(ctx.__name__))

    def translate_In(self, op):
        return ast.Eq() if isinstance(op, ast.In) else op

    def _rewrite_membership_op(self, node, left, right):
        return self.visit(node.op), node.op, left, right


class Expr(expr.Expr):

    """ hold a pytables like expression, comprised of possibly multiple 'terms'

    Parameters
    ----------
    where : string term expression, Expr, or list-like of Exprs
    queryables : a "kinds" map (dict of column name -> kind), or None if column
        is non-indexable
    encoding : an encoding that will encode the query terms

    Returns
    -------
    an Expr object

    Examples
    --------

    'index>=date'
    "columns=['A', 'D']"
    'columns=A'
    'columns==A'
    "~(columns=['A','B'])"
    'index>df.index[3] & string="bar"'
    '(index>df.index[3] & index<=df.index[6]) | string="bar"'
    "ts>=Timestamp('2012-02-01')"
    "major_axis>=20130101"
    """

    def __init__(self, where, op=None, value=None, queryables=None,
                 encoding=None, scope_level=0):

        # try to be back compat
        where = self.parse_back_compat(where, op, value)

        self.encoding = encoding
        self.condition = None
        self.filter = None
        self.terms = None
        self._visitor = None

        # capture the environment if needed
        local_dict = DeepChainMap()

        if isinstance(where, Expr):
            local_dict = where.env.scope
            where = where.expr

        elif isinstance(where, (list, tuple)):
            for idx, w in enumerate(where):
                if isinstance(w, Expr):
                    local_dict = w.env.scope
                else:
                    w = self.parse_back_compat(w)
                    where[idx] = w
            where = ' & ' .join([f"({w})" for w in where])

        self.expr = where
        self.env = Scope(scope_level + 1, local_dict=local_dict)

        if queryables is not None and isinstance(self.expr, string_types):
            self.env.queryables.update(queryables)
            self._visitor = ExprVisitor(self.env, queryables=queryables,
                                        parser='pytables', engine='pytables',
                                        encoding=encoding)
            self.terms = self.parse()

    def parse_back_compat(self, w, op=None, value=None):
        """ allow backward compatibility for passed arguments """

        if isinstance(w, dict):
            w, op, value = w.get('field'), w.get('op'), w.get('value')
            if not isinstance(w, string_types):
                raise TypeError(
                    "where must be passed as a string if op/value are passed")
            warnings.warn("passing a dict to Expr is deprecated, "
                          "pass the where as a single string",
                          DeprecationWarning)
        if isinstance(w, tuple):
            if len(w) == 2:
                w, value = w
                op = '=='
            elif len(w) == 3:
                w, op, value = w
            warnings.warn("passing a tuple into Expr is deprecated, "
                          "pass the where as a single string",
                          DeprecationWarning)

        if op is not None:
            if not isinstance(w, string_types):
                raise TypeError(
                    "where must be passed as a string if op/value are passed")

            if isinstance(op, Expr):
                raise TypeError("invalid op passed, must be a string")
            w = "{0}{1}".format(w, op)
            if value is not None:
                if isinstance(value, Expr):
                    raise TypeError("invalid value passed, must be a string")

                # stringify with quotes these values
                def convert(v):
                    if isinstance(v, (datetime,np.datetime64,timedelta,np.timedelta64)) or hasattr(v, 'timetuple'):
                        return "'{0}'".format(v)
                    return v

                if isinstance(value, (list,tuple)):
                    value = [ convert(v) for v in value ]
                else:
                    value = convert(value)

                w = "{0}{1}".format(w, value)

            warnings.warn("passing multiple values to Expr is deprecated, "
                          "pass the where as a single string",
                          DeprecationWarning)

        return w

    def __unicode__(self):
        if self.terms is not None:
            return com.pprint_thing(self.terms)
        return com.pprint_thing(self.expr)

    def evaluate(self):
        """ create and return the numexpr condition and filter """

        try:
            self.condition = self.terms.prune(ConditionBinOp)
        except AttributeError:
            raise ValueError("cannot process expression [{0}], [{1}] is not a "
                             "valid condition".format(self.expr, self))
        try:
            self.filter = self.terms.prune(FilterBinOp)
        except AttributeError:
            raise ValueError("cannot process expression [{0}], [{1}] is not a "
                             "valid filter".format(self.expr, self))

        return self.condition, self.filter


class TermValue(object):

    """ hold a term value the we use to construct a condition/filter """

    def __init__(self, value, converted, kind):
        self.value = value
        self.converted = converted
        self.kind = kind

    def tostring(self, encoding):
        """ quote the string if not encoded
            else encode and return """
        if self.kind == u('string'):
            return self.converted if encoding is not None else f'"{self.converted}"'
        return self.converted


def maybe_expression(s):
    """ loose checking if s is a pytables-acceptable expression """
    if not isinstance(s, string_types):
        return False
    ops = ExprVisitor.binary_ops + ExprVisitor.unary_ops + ('=',)

    # make sure we have an op at least
    return any(op in s for op in ops)
