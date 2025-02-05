from __future__ import division, absolute_import, print_function

import sys, re, inspect, textwrap, pydoc
import sphinx
import collections
from .docscrape import NumpyDocString, FunctionDoc, ClassDoc

if sys.version_info[0] >= 3:
    sixu = lambda s: s
else:
    sixu = lambda s: unicode(s, 'unicode_escape')


class SphinxDocString(NumpyDocString):
    def __init__(self, docstring, config={}):
        # Subclasses seemingly do not call this.
        NumpyDocString.__init__(self, docstring, config=config)

    def load_config(self, config):
        self.use_plots = config.get('use_plots', False)
        self.class_members_toctree = config.get('class_members_toctree', True)

    # string conversion routines
    def _str_header(self, name, symbol='`'):
        return [f'.. rubric:: {name}', '']

    def _str_field_list(self, name):
        return [f':{name}:']

    def _str_indent(self, doc, indent=4):
        return [' '*indent + line for line in doc]

    def _str_signature(self):
        return ['']

    def _str_summary(self):
        return self['Summary'] + ['']

    def _str_extended_summary(self):
        return self['Extended Summary'] + ['']

    def _str_returns(self):
        out = []
        if self['Returns']:
            out += self._str_field_list('Returns')
            out += ['']
            for param, param_type, desc in self['Returns']:
                if param_type:
                    out += self._str_indent([f'**{param.strip()}** : {param_type}'])
                else:
                    out += self._str_indent([param.strip()])
                if desc:
                    out += ['']
                    out += self._str_indent(desc, 8)
                out += ['']
        return out

    def _str_param_list(self, name):
        out = []
        if self[name]:
            out += self._str_field_list(name)
            out += ['']
            for param, param_type, desc in self[name]:
                if param_type:
                    out += self._str_indent([f'**{param.strip()}** : {param_type}'])
                else:
                    out += self._str_indent([f'**{param.strip()}**'])
                if desc:
                    out += ['']
                    out += self._str_indent(desc, 8)
                out += ['']
        return out

    @property
    def _obj(self):
        if hasattr(self, '_cls'):
            return self._cls
        elif hasattr(self, '_f'):
            return self._f
        return None

    def _str_member_list(self, name):
        """
        Generate a member listing, autosummary:: table where possible,
        and a table where not.

        """
        out = []
        if self[name]:
            out += [f'.. rubric:: {name}', '']
            prefix = getattr(self, '_name', '')

            if prefix:
                prefix = f'~{prefix}.'

            autosum = []
            others = []
            for param, param_type, desc in self[name]:
                param = param.strip()

                # Check if the referenced member can have a docstring or not
                param_obj = getattr(self._obj, param, None)
                if not (callable(param_obj)
                        or isinstance(param_obj, property)
                        or inspect.isgetsetdescriptor(param_obj)):
                    param_obj = None

                if param_obj and (pydoc.getdoc(param_obj) or not desc):
                    # Referenced object has a docstring
                    autosum += [f"   {prefix}{param}"]
                else:
                    others.append((param, param_type, desc))

            if autosum:
                out += ['.. autosummary::']
                if self.class_members_toctree:
                    out += ['   :toctree:']
                out += [''] + autosum

            if others:
                maxlen_0 = max(3, max(len(x[0]) for x in others))
                hdr = sixu("=")*maxlen_0 + sixu("  ") + sixu("=")*10
                fmt = sixu('%%%ds  %%s  ') % (maxlen_0,)
                out += ['', hdr]
                for param, param_type, desc in others:
                    desc = sixu(" ").join(x.strip() for x in desc).strip()
                    if param_type:
                        desc = f"({param_type}) {desc}"
                    out += [fmt % (param.strip(), desc)]
                out += [hdr]
            out += ['']
        return out

    def _str_section(self, name):
        out = []
        if self[name]:
            out += self._str_header(name)
            out += ['']
            content = textwrap.dedent("\n".join(self[name])).split("\n")
            out += content
            out += ['']
        return out

    def _str_see_also(self, func_role):
        out = []
        if self['See Also']:
            see_also = super(SphinxDocString, self)._str_see_also(func_role)
            out = ['.. seealso::', '']
            out += self._str_indent(see_also[2:])
        return out

    def _str_warnings(self):
        out = []
        if self['Warnings']:
            out = ['.. warning::', '']
            out += self._str_indent(self['Warnings'])
        return out

    def _str_index(self):
        idx = self['index']
        out = []
        if len(idx) == 0:
            return out

        out += [f".. index:: {idx.get('default', '')}"]
        for section, references in idx.items():
            if section == 'default':
                continue
            elif section == 'refguide':
                out += [f"   single: {', '.join(references)}"]
            else:
                out += [f"   {section}: {','.join(references)}"]
        return out

    def _str_references(self):
        out = []
        if self['References']:
            out += self._str_header('References')
            if isinstance(self['References'], str):
                self['References'] = [self['References']]
            out.extend(self['References'])
            out += ['']
            # Latex collects all references to a separate bibliography,
            # so we need to insert links to it
            if sphinx.__version__ >= "0.6":
                out += ['.. only:: latex','']
            else:
                out += ['.. latexonly::','']
            items = []
            for line in self['References']:
                if m := re.match(r'.. \[([a-z0-9._-]+)\]', line, re.I):
                    items.append(m.group(1))
            out += ['   ' + ", ".join([f"[{item}]_" for item in items]), '']
        return out

    def _str_examples(self):
        examples_str = "\n".join(self['Examples'])

        if (
            not self.use_plots
            or 'import matplotlib' not in examples_str
            or 'plot::' in examples_str
        ):
            return self._str_section('Examples')
        out = []
        out += self._str_header('Examples')
        out += ['.. plot::', '']
        out += self._str_indent(self['Examples'])
        out += ['']
        return out

    def __str__(self, indent=0, func_role="obj"):
        out = []
        out += self._str_signature()
        out += self._str_index() + ['']
        out += self._str_summary()
        out += self._str_extended_summary()
        out += self._str_param_list('Parameters')
        out += self._str_returns()
        for param_list in ('Other Parameters', 'Raises', 'Warns'):
            out += self._str_param_list(param_list)
        out += self._str_warnings()
        out += self._str_see_also(func_role)
        out += self._str_section('Notes')
        out += self._str_references()
        out += self._str_examples()
        for param_list in ('Attributes', 'Methods'):
            out += self._str_member_list(param_list)
        out = self._str_indent(out,indent)
        return '\n'.join(out)

class SphinxFunctionDoc(SphinxDocString, FunctionDoc):
    def __init__(self, obj, doc=None, config={}):
        self.load_config(config)
        FunctionDoc.__init__(self, obj, doc=doc, config=config)

class SphinxClassDoc(SphinxDocString, ClassDoc):
    def __init__(self, obj, doc=None, func_doc=None, config={}):
        self.load_config(config)
        ClassDoc.__init__(self, obj, doc=doc, func_doc=None, config=config)

class SphinxObjDoc(SphinxDocString):
    def __init__(self, obj, doc=None, config={}):
        self._f = obj
        self.load_config(config)
        SphinxDocString.__init__(self, doc, config=config)

def get_doc_object(obj, what=None, doc=None, config={}):
    if what is None:
        if inspect.isclass(obj):
            what = 'class'
        elif inspect.ismodule(obj):
            what = 'module'
        elif isinstance(obj, collections.Callable):
            what = 'function'
        else:
            what = 'object'
    if what == 'class':
        return SphinxClassDoc(obj, func_doc=SphinxFunctionDoc, doc=doc,
                              config=config)
    elif what in ('function', 'method'):
        return SphinxFunctionDoc(obj, doc=doc, config=config)
    else:
        if doc is None:
            doc = pydoc.getdoc(obj)
        return SphinxObjDoc(obj, doc, config=config)
