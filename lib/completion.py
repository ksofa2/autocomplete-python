import os
import io
import sys
import json
import traceback
sys.path.append(os.path.dirname(__file__))
import jedi
# remove jedi from path after we import it so it will not be completed
sys.path.pop(0)


class JediCompletion(object):
    basic_types = {
        'module': 'import',
        'instance': 'variable',
        'statement': 'value',
        'keyword': 'keyword',
    }

    def __init__(self):
        self.default_sys_path = sys.path
        self._input = io.open(sys.stdin.fileno(), encoding='utf-8')

    def _get_definition_type(self, definition):
        is_built_in = definition.in_builtin_module
        if definition.type not in ['import', 'keyword'] and is_built_in():
            return 'builtin'
        if definition.type in ['statement'] and definition.name.isupper():
            return 'constant'
        return self.basic_types.get(definition.type, definition.type)

    def _additional_info(self, completion):
        """Provide additional information about the completion object."""
        if completion._definition is None:
            return ''
        if completion.type == 'statement':
            nodes_to_display = ['InstanceElement', 'String', 'Node', 'Lambda',
                                'Number']
            return ''.join(c.get_code() for c in
                           completion._definition.children if type(c).__name__
                           in nodes_to_display).replace('\n', '')
        return ''

    @classmethod
    def _get_top_level_module(cls, path):
        """Recursively walk through directories looking for top level module.

        Jedi will use current filepath to look for another modules at same
        path, but it will not be able to see modules **above**, so our goal
        is to find the higher python module available from filepath.
        """
        _path, _ = os.path.split(path)
        if os.path.isfile(os.path.join(_path, '__init__.py')):
            return cls._get_top_level_module(_path)
        return path

    def _generate_signature(self, completion):
        """Generate signature with function arguments.
        """
        if not hasattr(completion, 'params'):
            return ''
        return '%s(%s)' % (
            completion.name,
            ', '.join(param.description for param in completion.params))

    def _serialize_completions(self, script, identifier=None):
        """Serialize response to be read from Atom.

        Args:
          script: Instance of jedi.api.Script object.
          identifier: Unique completion identifier to pass back to Atom.

        Returns:
          Serialized string to send to Atom.
        """
        _completions = []

        try:
            call_signatures = script.call_signatures()
        except KeyError:
            call_signatures = []
        for call_signature in call_signatures:
            for pos, param in enumerate(call_signature.params):
                if not param.name:
                    continue
                if param.name == 'self' and pos == 0:
                    continue
                try:
                    name, value = param.description.split('=')
                except ValueError:
                    name = param.description
                    value = None
                _completion = {
                    'type': 'variable',
                    'rightLabel': self._additional_info(call_signature)
                }
                if value:
                    _completion['snippet'] = '%s=${1:%s}$0' % (name, value)
                else:
                    _completion['snippet'] = '%s=$1$0' % name
                if self.show_doc_strings:
                    _completion['description'] = call_signature.docstring()
                else:
                    _completion['description'] = self._generate_signature(
                        call_signature)
                _completions.append(_completion)

        try:
            completions = script.completions()
        except KeyError:
            completions = []
        for completion in completions:
            if self.show_doc_strings:
                description = completion.docstring()
            else:
                description = self._generate_signature(completion)
            _completion = {
                'snippet': '%s$0' % completion.name,
                'type': self._get_definition_type(completion),
                'description': description,
                'rightLabel': self._additional_info(completion)
            }
            _completions.append(_completion)
        return json.dumps({'id': identifier, 'results': _completions})

    def _serialize_arguments(self, script, identifier=None):
        """Serialize response to be read from Atom.

        Args:
          script: Instance of jedi.api.Script object.
          identifier: Unique completion identifier to pass back to Atom.

        Returns:
          Serialized string to send to Atom.
        """
        arguments = []
        i = 1
        try:
            call_signatures = script.call_signatures()
        except KeyError:
            call_signatures = []
        for call_signature in call_signatures:
            for pos, param in enumerate(call_signature.params):
                if not param.name:
                    continue
                if param.name == 'self' and pos == 0:
                    continue
                try:
                    name, value = param.description.split('=')
                except ValueError:
                    name = param.description
                    value = None
                if name.startswith('*'):
                    continue
                if not value:
                    arguments.append('${%s:%s}' % (i, name))
                elif self.use_snippets == 'all':
                    arguments.append('%s=${%s:%s}' % (name, i, value))
                i += 1
        snippet = '%s$0' % ', '.join(arguments)
        return json.dumps({'id': identifier, 'results': [],
                           'arguments': snippet})

    def _serialize_definitions(self, definitions, identifier=None):
        """Serialize response to be read from Atom.

        Args:
          definitions: List of jedi.api.classes.Definition objects.
          identifier: Unique completion identifier to pass back to Atom.

        Returns:
          Serialized string to send to Atom.
        """
        _definitions = []
        for definition in definitions:
            if definition.module_path:
                _definition = {
                    'text': definition.name,
                    'type': self._get_definition_type(definition),
                    'fileName': definition.module_path,
                    'line': definition.line - 1,
                    'column': definition.column
                }
                _definitions.append(_definition)
        return json.dumps({'id': identifier, 'results': _definitions})

    def _deserialize(self, request):
        """Deserialize request from Atom.

        Args:
          request: String with raw request from Atom.

        Returns:
          Python dictionary with request data.
        """
        return json.loads(request)

    def _set_request_config(self, config):
        """Sets config values for current request including sys.path
        modifications.

        The sys.path is getting restored to default value on each request
        so each project should be isolated from each other.

        Args:
          config: Dictionary with config values.
        """
        sys.path = self.default_sys_path
        self.use_snippets = config.get('useSnippets')
        self.show_doc_strings = config.get('showDescriptions', True)
        jedi.settings.case_insensitive_completion = config.get(
            'caseInsensitiveCompletion', True)
        for path in config.get('extraPaths', []):
            if path and path not in sys.path:
                sys.path.insert(0, path)

    def _process_request(self, request):
        """Accept serialized request from Atom and write response.
        """
        request = self._deserialize(request)

        self._set_request_config(request.get('config', {}))

        path = self._get_top_level_module(request.get('path', ''))
        if path not in sys.path:
            sys.path.insert(0, path)
        lookup = request.get('lookup', 'completions')

        script = jedi.api.Script(
            source=request['source'], line=request['line'] + 1,
            column=request['column'], path=request.get('path', ''))

        if lookup == 'definitions':
            return self._write_response(self._serialize_definitions(
                script.goto_assignments(), request['id']))
        elif lookup == 'arguments':
            return self._write_response(self._serialize_arguments(
                script, request['id']))
        else:
            return self._write_response(
                self._serialize_completions(script, request['id']))

    def _write_response(self, response):
        sys.stdout.write(response + '\n')
        sys.stdout.flush()

    def watch(self):
        while True:
            try:
                self._process_request(self._input.readline())
            except Exception:
                sys.stderr.write(traceback.format_exc() + '\n')

if __name__ == '__main__':
    JediCompletion().watch()
