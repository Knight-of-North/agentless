import ast
import os
import re
import shutil
import subprocess
import uuid
from collections import OrderedDict
from pathlib import Path

from get_repo_structure.get_patch_info import parse_patch


def check_syntax(code):
    if not isinstance(code, list):
        code = [code]

    for c in code:
        if (
            not c.strip()
        ):  # Check for cases where the model didn't return a python block
            return False
        try:
            ast.parse(c)
        except SyntaxError as e:
            return False
    return True


def remove_empty_lines(code: str) -> str:
    # Split the code into lines
    lines = code.splitlines()
    # Remove empty lines
    filtered_lines = [line for line in lines if line.strip() != ""]
    return "\n".join(filtered_lines)


def check_code_differ_by_just_empty_lines(codes, prev_codes) -> bool:

    if not isinstance(codes, list):
        codes = [codes]
        prev_codes = [prev_codes]

    normalized_code1 = ""
    normalized_code2 = ""

    for code, prev_code in zip(codes, prev_codes):
        # Normalize both code snippets
        normalized_code1 += remove_empty_lines(code)
        normalized_code2 += remove_empty_lines(prev_code)

    return normalized_code1 == normalized_code2


def _run_flake8(file_path: Path):
    """Return the set of flake8 messages, or None if flake8 is unavailable."""
    flake8 = shutil.which("flake8")
    if flake8 is None:
        return None
    fatal = "E9,F821,F823,F831,F406,F407,F701,F702,F704,F706"
    result = subprocess.run(
        [
            flake8,
            f"--select={fatal}",
            "--isolated",
            "--format=%(row)d:%(col)d:%(code)s:%(text)s",
            str(file_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # flake8 exits 1 when it reports violations; anything else is a tool error.
    if result.returncode not in (0, 1):
        return None
    errors = set()
    for line in result.stdout.splitlines():
        parts = line.split(":", 3)
        if len(parts) == 4 and parts[0].strip().isdigit():
            errors.add(parts[3].strip())
    return errors


def lint_code(repo_playground, temp_name, code, prev_code="") -> tuple[bool, set, set]:
    """Check that a candidate edit does not introduce new fatal lint errors."""
    playground = Path(repo_playground) / str(uuid.uuid4())
    assert not playground.exists(), f"{playground} already exists"
    playground.mkdir(parents=True)

    temp_path = playground / temp_name
    try:
        temp_path.write_text(prev_code, encoding="utf-8")
        prev_errors = _run_flake8(temp_path)

        temp_path.write_text(code, encoding="utf-8")
        errors = _run_flake8(temp_path)

        # If flake8 is not installed, at least reject syntax errors instead of
        # silently treating every candidate as valid.
        if prev_errors is None or errors is None:
            try:
                compile(code, str(temp_path), "exec")
            except SyntaxError:
                return False, set(), {"syntax error"}
            return True, set(), set()

        if len(errors - prev_errors) > 0:
            return False, prev_errors, errors

        return True, set(), set()
    finally:
        shutil.rmtree(playground, ignore_errors=True)


def _run_git(args, cwd: Path):
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()}"
        )
    return result


def _init_fake_repo(repo_path: Path):
    _run_git(["init", "-q"], repo_path)
    _run_git(["config", "user.email", "agentless@example.com"], repo_path)
    _run_git(["config", "user.name", "Agentless"], repo_path)


def fake_git_repo(repo_playground, file_pathes, old_contents, new_contents) -> str:
    """create a fake git repo to obtain git diff format"""

    if not isinstance(file_pathes, list):
        # for backwards compatibility
        file_pathes = [file_pathes]
        old_contents = [old_contents]
        new_contents = [new_contents]

    playground = Path(repo_playground) / str(uuid.uuid4())
    assert not playground.exists(), f"{playground} already exists"
    playground.mkdir(parents=True)

    try:
        _init_fake_repo(playground)

        for file_path, old_content in zip(file_pathes, old_contents):
            full_path = playground / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(old_content, encoding="utf-8")

        _run_git(["add", "--all"], playground)
        _run_git(["commit", "-m", "initial commit"], playground)

        for file_path, new_content in zip(file_pathes, new_contents):
            full_path = playground / file_path
            full_path.write_text(new_content, encoding="utf-8")

        return _run_git(["diff", "--no-color", "--"], playground).stdout
    finally:
        shutil.rmtree(playground, ignore_errors=True)


def fake_git_apply(repo_playground, file_path, old_content, patch) -> str:
    """create a fake git repo to obtain new file content"""

    playground = Path(repo_playground) / str(uuid.uuid4())
    assert not playground.exists(), f"{playground} already exists"
    playground.mkdir(parents=True)

    try:
        _init_fake_repo(playground)

        full_path = playground / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(old_content, encoding="utf-8")
        _run_git(["add", "--all"], playground)
        _run_git(["commit", "-m", "initial commit"], playground)

        patch_file = f"{uuid.uuid4()}.patch"
        patch_path = playground / patch_file
        patch_path.write_text(patch, encoding="utf-8")

        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", patch_file],
            cwd=str(playground),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print("stderr> ", result.stderr)
            # Some model patches omit a final newline; retry once with one.
            full_path.write_text(old_content + "\n", encoding="utf-8")
            result = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", patch_file],
                cwd=str(playground),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                print("stderr> ", result.stderr)
                raise RuntimeError("git apply failed for patch")

        return full_path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(playground, ignore_errors=True)


def fake_git_apply_multiple(repo_playground, file_path_contents, patch) -> dict:
    """create a fake git repo to obtain new file contents (multiple)"""

    playground = Path(repo_playground) / str(uuid.uuid4())
    assert not playground.exists(), f"{playground} already exists"
    playground.mkdir(parents=True)

    try:
        _init_fake_repo(playground)

        for file_path, old_content in file_path_contents.items():
            full_path = playground / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(old_content, encoding="utf-8")

        _run_git(["add", "--all"], playground)
        _run_git(["commit", "-m", "initial commit"], playground)

        patch_file = f"{uuid.uuid4()}.patch"
        patch_path = playground / patch_file
        patch_path.write_text(patch, encoding="utf-8")

        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", patch_file],
            cwd=str(playground),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print("stderr> ", result.stderr)
            # Some model patches omit a final newline; retry once with one.
            for file_path, old_content in file_path_contents.items():
                (playground / file_path).write_text(
                    old_content + "\n", encoding="utf-8"
                )
            result = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", patch_file],
                cwd=str(playground),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                print("stderr> ", result.stderr)
                raise RuntimeError("git apply failed for multi-file patch")

        return {
            file_path: (playground / file_path).read_text(encoding="utf-8")
            for file_path in file_path_contents
        }
    finally:
        shutil.rmtree(playground, ignore_errors=True)


def get_functions(tree):
    """Get a set of function and method names from the AST tree."""
    functions = {}

    class FunctionVisitor(ast.NodeVisitor):
        def __init__(self):
            self.parents = []

        def visit(self, node):
            self.parents.append(node)
            super().visit(node)
            self.parents.pop()

        def visit_FunctionDef(self, node):
            if not any(isinstance(parent, ast.ClassDef) for parent in self.parents):
                functions[node.name] = ast.unparse(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            if not any(isinstance(parent, ast.ClassDef) for parent in self.parents):
                functions[node.name] = ast.unparse(node)
            self.generic_visit(node)

    class ClassVisitor(ast.NodeVisitor):
        def visit_ClassDef(self, node):
            class_name = node.name
            for body_item in node.body:
                if isinstance(body_item, ast.FunctionDef) or isinstance(
                    body_item, ast.AsyncFunctionDef
                ):
                    functions[f"{class_name}.{body_item.name}"] = ast.unparse(body_item)
            self.generic_visit(node)

    FunctionVisitor().visit(tree)
    ClassVisitor().visit(tree)
    return functions


def is_just_new_function(code1, code2):
    tree1 = ast.parse(code1)
    tree2 = ast.parse(code2)

    functions1 = get_functions(tree1)
    functions2 = get_functions(tree2)

    # The new functions in the second code
    if len(set(list(functions1.keys())) - set(list(functions2.keys()))) > 0:
        # removes functions
        return False

    for func in functions1:
        if functions1[func] != functions2[func]:
            # modifies existing functions
            return False

    if len(set(list(functions2.keys())) - set(list(functions1.keys()))) > 0:
        return True

    # modifying global stuff is okay, because its actually same as functions almost.

    return False


import io
import re
import tokenize


def remove_comments_and_docstrings(source):
    io_obj = io.StringIO(source)
    out = ""
    prev_toktype = tokenize.INDENT
    last_lineno = -1
    last_col = 0
    for tok in tokenize.generate_tokens(io_obj.readline):
        token_type = tok[0]
        token_string = tok[1]
        start_line, start_col = tok[2]
        end_line, end_col = tok[3]
        ltext = tok[4]
        if start_line > last_lineno:
            last_col = 0
        if start_col > last_col:
            out += " " * (start_col - last_col)
        if token_type == tokenize.COMMENT:
            pass
        elif token_type == tokenize.STRING:
            if prev_toktype != tokenize.INDENT:
                if prev_toktype != tokenize.NEWLINE:
                    if start_col > 0:
                        out += token_string
        else:
            out += token_string
        prev_toktype = token_type
        last_col = end_col
        last_lineno = end_line
    out = "\n".join(l for l in out.splitlines() if l.strip())
    return out


def normalize_patch(
    instance_id,
    patch: str,
    original_file_content: list,
    new_file_content: list,
    edited_files: list,
) -> str:
    "Remove edits to trailing spaces and comments in the patch."
    if not patch.strip():
        return ""

    normalized_diff = ""

    for o_file_content, n_file_content, edited_file in zip(
        original_file_content, new_file_content, edited_files
    ):
        old_content = o_file_content
        new_content = n_file_content

        # Normalize file contents
        def normalize_code(code):
            try:
                node = ast.parse(code)
                return ast.unparse(node)
            except:
                return code

        old_content = normalize_code(old_content)
        new_content = normalize_code(new_content)

        try:
            remove_docstring_old_content = remove_comments_and_docstrings(old_content)
            ast.parse(remove_docstring_old_content)  # check
            remove_docstring_new_content = remove_comments_and_docstrings(new_content)
            ast.parse(remove_docstring_new_content)  # check
        except:
            # when does this exception happen?
            # when the code has some class or function with empty docstring (thats valid python code)
            # but removing it is not, to be save we just use the original.
            remove_docstring_old_content = old_content
            remove_docstring_new_content = new_content

        diff = fake_git_repo(
            "playground",
            edited_file,
            remove_docstring_old_content,
            remove_docstring_new_content,
        )

        if is_just_new_function(
            remove_docstring_old_content, remove_docstring_new_content
        ):
            # modify the diff to ignore context.
            new_diff = []
            for line in diff.splitlines():
                if line.startswith("-") or line.startswith("+"):
                    new_diff.append(line)
            diff = "\n".join(new_diff)

        normalized_diff += diff

    # Note that the normalized diff may not be applied to the original file.
    return normalized_diff


def extract_python_blocks(text):
    # Regular expression pattern to match ```python\n{text}\n```
    pattern = r"```python\n(.*?)\n```"

    # Use re.findall to find all matches
    matches = re.findall(pattern, text, re.DOTALL)

    return matches


def extract_code_blocks(text):
    pattern = r"```\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    if len(matches) == 0:
        if "```" in text:
            # handle the case where the code block is not complete
            return [text.split("```", 1)[-1].strip()]
    return matches


def extract_locs_for_files(locs, file_names, keep_old_order=False):
    if keep_old_order:
        results = {fn: [] for fn in file_names}
    else:
        results = {}  # dict is insertion ordered
    current_file_name = None
    for loc in locs:
        for line in loc.splitlines():
            if line.strip().endswith(".py"):
                current_file_name = line.strip()
            elif line.strip() and any(
                line.startswith(w)
                for w in ["line:", "function:", "class:", "variable:"]
            ):
                if current_file_name in file_names:
                    if current_file_name not in results:
                        results[current_file_name] = []
                    results[current_file_name].append(line)
                else:
                    pass

    for file_name in file_names:
        if file_name not in results:  # guard for new order case
            results[file_name] = []

    return {fn: ["\n".join(results[fn])] for fn in results.keys()}


def extract_starting_number(subcommand):
    return int(subcommand.split(",")[0].split("start=")[-1])


def extract_ending_number(subcommand):
    return int(subcommand.split(",")[1].split("end=")[-1])


def overlap(subcommand1, subcommand2):
    start1, end1 = extract_starting_number(subcommand1), extract_ending_number(
        subcommand1
    )
    start2, end2 = extract_starting_number(subcommand2), extract_ending_number(
        subcommand2
    )
    return not (end1 < start2 or end2 < start1)


def split_edit_multifile_commands(
    commands, diff_format=False, str_replace_format=False
) -> dict[str, str]:
    """Split commands based on edited files."""
    file_to_commands = OrderedDict()
    if str_replace_format:
        for command in commands:
            file_name = None
            for json_message in command:
                if json_message["type"] == "tool_use":
                    str_replace_command = json_message["input"]

                    if "command" not in str_replace_command:
                        str_replace_command["command"] = "str_replace"

                    if "path" not in str_replace_command:
                        continue

                    if "command" not in str_replace_command:
                        continue

                    if str_replace_command["command"] == "str_replace":
                        if "old_str" not in str_replace_command:
                            continue

                        if "new_str" not in str_replace_command:
                            # add empty string
                            str_replace_command["new_str"] = ""

                    if str_replace_command["command"] == "insert":
                        if (
                            "insert_line" not in str_replace_command
                            or "new_str" not in str_replace_command
                        ):
                            continue

                    file_name = "'" + str_replace_command["path"] + "'"
                    # deduplicate
                    if (
                        file_name not in file_to_commands
                        or str_replace_command not in file_to_commands[file_name]
                    ):
                        file_to_commands.setdefault(file_name, []).append(
                            str_replace_command
                        )

    elif diff_format:
        for command in commands:
            file_name = None
            for subcommand in command.split(">>>>>>> REPLACE")[:-1]:
                subcommand = subcommand.strip()
                if "<<<<<<< SEARCH" in subcommand:
                    fn = subcommand.split("<<<<<<< SEARCH")[0].lstrip("#").strip()
                    if fn:
                        file_name = "'" + fn + "'"

                if len(subcommand.split("<<<<<<< SEARCH")) != 2:
                    continue
                converted_command = (
                    "<<<<<<< SEARCH"
                    + subcommand.split("<<<<<<< SEARCH")[1]
                    + "\n"
                    + ">>>>>>> REPLACE"
                )
                # deduplicate
                if (
                    file_name not in file_to_commands
                    or converted_command not in file_to_commands[file_name]
                ):
                    file_to_commands.setdefault(file_name, []).append(converted_command)

    else:
        for command in commands:
            for subcommand in command.split("edit_file(")[1:]:
                file_name, start, end, content = subcommand.split(",", 3)
                converted_command = "edit_file(" + ",".join([start, end, content])
                # deduplicate
                if (
                    file_name not in file_to_commands
                    or converted_command not in file_to_commands[file_name]
                ):
                    file_to_commands.setdefault(file_name, []).append(converted_command)

    return file_to_commands


def parse_str_replace_edit_commands(
    commands, content, file_loc_intervals: list[tuple[int, int]]
):
    """
    Original implementation: https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/computer_use_demo/tools/edit.py
    """
    # let's first make sure the intervals are sorted
    file_loc_intervals.sort()
    replaced = False
    # apply the edits from the end of file to the beginning of file
    # this is to make sure context is correct
    for interval in file_loc_intervals[::-1]:
        start, end = interval
        start = max(start - 1, 0)
        # intervals are 1-indexed

        context_segment = "\n".join(content.splitlines()[start:end])
        context_segment = "\n" + context_segment + "\n"

        # since we want to replace the original context, let's first check for all edits.
        can_apply = []
        for subcommand in commands:

            if subcommand["command"] == "str_replace":
                original, replace = subcommand["old_str"], subcommand["new_str"]

                if original in context_segment:
                    can_apply.append(subcommand)

            elif subcommand["command"] == "insert":
                insert_line = subcommand["insert_line"]
                # TODO check this
                if insert_line > start and insert_line <= end:
                    can_apply.append(subcommand)

        # apply edits backwards
        for subcommand in can_apply[::-1]:

            if subcommand["command"] == "str_replace":
                original, replace = subcommand["old_str"], subcommand["new_str"]

                if (
                    original in context_segment
                ):  # This may not be true after some previously applied edits
                    context_segment = context_segment.replace(original, replace)
                    replaced = True

            elif subcommand["command"] == "insert":
                insert_line = subcommand["insert_line"]
                replace = subcommand["new_str"]
                # TODO check this
                if insert_line > start and insert_line <= end:

                    if insert_line != start - 1:
                        replace = "\n" + replace
                    if insert_line != end:
                        replace = replace + "\n"
                    context_segment = (
                        "\n".join(
                            context_segment.splitlines()[: insert_line - start + 1]
                        )
                        + replace
                        + "\n".join(
                            context_segment.splitlines()[insert_line - start + 1 :]
                        )
                    )
                    context_segment = context_segment + "\n"
                    replaced = True

        # reassembly
        content = (
            "\n".join(content.splitlines()[:start])
            + context_segment
            + "\n".join(content.splitlines()[end:])
        )

    if not replaced:
        print("not replaced")

    return content


def parse_diff_edit_commands(
    commands, content, file_loc_intervals: list[tuple[int, int]]
):
    def parse_for_threedots(original, replace, file_loc_intervals, content):
        # if dot dot dot in replace, its always safe to remove it
        if replace.startswith("...\n") and len(replace) > 4:
            # im parsing this first because its used later on
            replace = replace[4:]

        # just dot dot dot, then need to do something special
        if original == "...":
            if not replace[0].isspace():
                # this is okay
                # find a suitable original string to replace
                for interval in file_loc_intervals:
                    start, end = interval
                    start = max(start - 1, 0)
                    context_segment = "\n".join(content.splitlines()[start:end])

                    for line in context_segment.splitlines():
                        if len(line) > 0 and not line[0].isspace():
                            if content.count(line) == 1:
                                original = line
                                # keep the line
                                replace = replace + "\n\n" + line
                                break

                    if original != "...":
                        break

                if original == "...":
                    print("cannot find suitable location")

        # dot dot dot with something else in original, then its safe to replace
        if original.startswith("...\n") and len(original) > 4:
            # remove dot dot dot from original
            original = original[4:]

        return original, replace

    if len(file_loc_intervals) == 0:
        if original in content:
            content = content.replace(original, replace)
            replaced = True
        else:
            print("not replaced")
        return content
    # let's first make sure the intervals are sorted
    file_loc_intervals.sort()
    replaced = False
    # apply the edits from the end of file to the beginning of file
    # this is to make sure context is correct
    for interval in file_loc_intervals[::-1]:
        start, end = interval
        start = max(start - 1, 0)
        context_segment = "\n".join(content.splitlines()[start:end])
        context_segment = "\n" + context_segment + "\n"

        # since we want to replace the original context, let's first check for all edits.
        can_apply = []
        for subcommand in commands:
            if not subcommand.startswith("<<<<<<< SEARCH") and subcommand.endswith(
                ">>>>>>> REPLACE"
            ):
                continue

            subcommand = "\n".join(subcommand.splitlines()[1:-1])
            if len(subcommand.split("\n=======\n")) != 2:
                continue

            original, replace = subcommand.split("\n=======\n")

            original, replace = parse_for_threedots(
                original, replace, file_loc_intervals, content
            )

            original = "\n" + original + "\n"
            replace = "\n" + replace + "\n"

            if original in context_segment:
                can_apply.append(subcommand)

        # apply edits backwards
        for subcommand in can_apply[::-1]:
            original, replace = subcommand.split("\n=======\n")

            original, replace = parse_for_threedots(
                original, replace, file_loc_intervals, content
            )

            original = "\n" + original + "\n"
            replace = "\n" + replace + "\n"
            if (
                original in context_segment
            ):  # This may not be true after some previously applied edits
                context_segment = context_segment.replace(original, replace)
                replaced = True
        # reassembly
        content = (
            "\n".join(content.splitlines()[:start])
            + context_segment
            + "\n".join(content.splitlines()[end:])
        )

    if not replaced:
        print("not replaced")

    return content


def _parse_literal_content(value):
    """Best-effort literal parser that tolerates raw newlines in model output."""
    value = value.strip()
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        pass
    for quote in ('"""', "'''", '"', "'"):
        if (
            len(value) >= 2 * len(quote)
            and value.startswith(quote)
            and value.endswith(quote)
        ):
            inner = value[len(quote) : -len(quote)]
            # Malformed model output sometimes wraps the desired text in an
            # extra pair of triple quotes, e.g. ``"""\n"""text"""\n"""``.
            # Drop the empty quoted fragments and keep the real content.
            if len(quote) == 3 and quote in inner:
                fragments = [part.strip("\r\n") for part in inner.split(quote)]
                fragments = [part for part in fragments if part.strip()]
                if fragments:
                    return "\n".join(fragments)
            return (
                inner.replace("\\r\\n", "\n")
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\'", "'")
            )
    return None


def _clean_triple_quoted_content(value):
    """Strip stray triple-quote wrappers left by malformed model output."""
    if isinstance(value, str) and '"""' in value:
        fragments = [part.strip("\r\n") for part in value.split('"""')]
        fragments = [part for part in fragments if part.strip()]
        if fragments:
            return "\n".join(fragments)
    return value


def _parse_edit_file_subcommand(subcommand):
    """Parse ``edit_file(start, end, content)`` without executing code."""
    text = subcommand.strip()
    if text.startswith("edit_file("):
        text = text[len("edit_file(") :]
    # ``parse_edit_commands`` splits on ``edit_file(`` and therefore leaves the
    # closing parenthesis in the subcommand.  Remove it before re-wrapping the
    # arguments in a function call for the AST parser.
    if text.endswith(")"):
        text = text[:-1].rstrip()

    try:
        call = ast.parse(f"edit_file({text})", mode="eval").body
    except SyntaxError:
        call = None

    if (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "edit_file"
    ):
        if len(call.args) >= 3 and not call.keywords:
            try:
                start = ast.literal_eval(call.args[0])
                end = ast.literal_eval(call.args[1])
                content = _clean_triple_quoted_content(ast.literal_eval(call.args[2]))
                return int(start), int(end), str(content)
            except (ValueError, SyntaxError, TypeError):
                pass
        try:
            kwargs = {
                kw.arg: ast.literal_eval(kw.value)
                for kw in call.keywords
                if kw.arg is not None
            }
        except (ValueError, SyntaxError):
            kwargs = {}
        if all(key in kwargs for key in ("start", "end", "content")):
            return (
                int(kwargs["start"]),
                int(kwargs["end"]),
                str(_clean_triple_quoted_content(kwargs["content"])),
            )

    # Fallback for imperfectly formatted model output.  Only parse the numeric
    # arguments and the content string; never call eval().
    positional = re.match(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(.+?)\s*$", text, re.S)
    if positional:
        content_value = _parse_literal_content(positional.group(3))
        if content_value is not None:
            return int(positional.group(1)), int(positional.group(2)), str(content_value)
        # Some model outputs lose their quotes entirely; accept the bare text
        # rather than dropping the whole edit command.
        bare_content = positional.group(3).strip()
        if bare_content:
            return int(positional.group(1)), int(positional.group(2)), bare_content

    start_match = re.search(r"\bstart\s*=\s*(-?\d+)", text)
    end_match = re.search(r"\bend\s*=\s*(-?\d+)", text)
    content_match = re.search(r"\bcontent\s*=\s*(.*)", text, re.S)
    if not (start_match and end_match and content_match):
        return None

    raw_content = content_match.group(1).strip()
    variants = [raw_content]
    if raw_content.endswith(")"):
        variants.append(raw_content[:-1].rstrip())
    for value in variants:
        try:
            return (
                int(start_match.group(1)),
                int(end_match.group(1)),
                str(ast.literal_eval(value)),
            )
        except (ValueError, SyntaxError):
            pass

    # Last-resort handling for triple/single quoted content with a trailing
    # parenthesis or an unbalanced quote in the model output.
    for quote in ('"""', "'''", '"', "'"):
        if raw_content.startswith(quote) and raw_content.endswith(quote):
            raw_content = raw_content[len(quote) : -len(quote)]
            break
    return int(start_match.group(1)), int(end_match.group(1)), raw_content


def parse_edit_commands(commands, content):
    content_lines = content.splitlines()
    map_content_lines_to_line_num = [i for i in range(0, len(content_lines) + 1)]

    # Make a list of the subcommands
    subcommands = []

    for command in commands:
        for subcommand in command.split("edit_file(")[1:]:
            subcommands.append(subcommand)

    # Remove duplicates while preserving order
    seen = set()
    unique_subcommands = []
    for subcommand in subcommands:
        if subcommand not in seen:
            unique_subcommands.append(subcommand)
            seen.add(subcommand)

    # Sort the unique subcommands by the starting number in reverse order
    unique_sorted_subcommands = sorted(
        unique_subcommands, key=extract_starting_number, reverse=True
    )

    for subcommand in unique_sorted_subcommands:
        parsed = _parse_edit_file_subcommand(subcommand)
        if parsed is None:
            continue
        command_start, command_end, changed_content = parsed

        if command_start not in map_content_lines_to_line_num:
            continue
        if command_end not in map_content_lines_to_line_num:
            continue
        start = map_content_lines_to_line_num.index(command_start)
        end = map_content_lines_to_line_num.index(command_end)

        # small thing to ensure right white space indent
        if (
            start == end
            and len(changed_content.splitlines()) == 1
            and (len(changed_content) - len(changed_content.lstrip())) == 0
        ):
            indent_length = len(content_lines[start - 1]) - len(
                content_lines[start - 1].lstrip()
            )
            changed_content = " " * indent_length + changed_content.lstrip()

        content_lines[start - 1 : end] = changed_content.splitlines()

    content = "\n".join(content_lines)

    return content


def test_parse_str_replace():

    content = """A
B
C
D
E
F
""".strip()

    from agentless.util.preprocess_data import line_wrap_content

    shown_content = line_wrap_content(content, context_intervals=[(3, 5)])
    print(shown_content)

    commands = [
        {"command": "insert", "path": "test", "insert_line": 1, "new_str": "test_str"}
    ]
    new_content = parse_str_replace_edit_commands(
        commands, content, file_loc_intervals=[(1, 5)]
    )
    print(new_content)


def test_parse():
    raw_output = """
```python
edit_file(1, 1, "import os")
```
"""

    content = """
import sys
""".strip()

    commands = extract_python_blocks(raw_output)

    content = parse_edit_commands(commands, content)

    assert content == "import os", content

    raw_output = """
```python
edit_file(1, 1, '''import os\nimport sys''')
```
"""

    content = """
import sys
""".strip()

    commands = extract_python_blocks(raw_output)

    content = parse_edit_commands(commands, content)

    assert content == "import os\nimport sys", content

    raw_output = """
```python
edit_file(1, 1, '''import os''')
edit_file(1, 1, '''import sys''')
```
"""

    content = """
import sys
""".strip()

    commands = extract_python_blocks(raw_output)

    content = parse_edit_commands(commands, content)

    assert content == "import sys", content

    content = """
test
testing
""".strip()
    raw_output = """
```python
edit_file(1, 1, "testing\ntesting2")
edit_file(2, 2, "testing3")
```
"""
    content = parse_edit_commands(extract_python_blocks(raw_output), content)

    assert content == "testing\ntesting2\ntesting3", content

    content = """
test
testing
testinging
""".strip()

    raw_output = """
```python
edit_file(1, 2, "testing")
edit_file(3, 3, "testing3")
```
"""
    content = parse_edit_commands(extract_python_blocks(raw_output), content)

    assert content == "testing\ntesting3", content

    content = """
test
testing
testinging
test
testing
""".strip()

    raw_output = """
```python
edit_file(1, 2, "testing")
edit_file(3, 3, "testing3")
edit_file(4, 4, "testing\ntesting2")
edit_file(5, 5, "testing3")
```
"""

    edited_content = """
testing
testing3
testing
testing2
testing3
""".strip()
    content = parse_edit_commands(extract_python_blocks(raw_output), content)

    assert content == edited_content, content

    # Test for if command is present twice in the output (adapted from real output)
    content = """
test
testing
testinging
test
testing
""".strip()

    raw_output = (
        """
"To fix the issue where `viewcode`
1. Add a condition

```python
edit_file(4, 4, """
        """
test4
"""
        """)
```

2. Ensure that the `doctree_read`

```python
edit_file(2, 3, """
        """
test-2-3
"""
        """)
```

Here is the complete set of commands to address the issue:

```python
edit_file(4, 4, """
        """
test4
"""
        """)
```

```python
edit_file(2, 3, """
        """
test-2-3
"""
        """)
```
"""
    )
    content = parse_edit_commands(extract_python_blocks(raw_output), content)

    revised_content = """
test
test-2-3
test4
testing
""".strip()

    assert content == revised_content, content

    raw_output = """
```
django/db/migrations/optimizer.py
function: MigrationOptimizer.optimize_inner

django/db/migrations/operations/fields.py
function: AlterField.reduce
```
"""
    files = [
        "django/db/migrations/optimizer.py",
        "django/db/migrations/operations/fields.py",
        "django/db/migrations/operations/models.py",
    ]
    extracted_locs = extract_locs_for_files([raw_output], files)
    print(extracted_locs)
    assert extracted_locs == {
        "django/db/migrations/optimizer.py": [
            "function: MigrationOptimizer.optimize_inner"
        ],
        "django/db/migrations/operations/fields.py": ["function: AlterField.reduce"],
        "django/db/migrations/operations/models.py": [""],
    }

    raw_output = """
```python
edit_file(start=1, end=1, content="testing not")
```
"""

    content = """
testing
""".strip()

    edited_content = """
testing not
""".strip()

    content = parse_edit_commands(extract_python_blocks(raw_output), content)
    assert content == edited_content, edited_content


if __name__ == "__main__":
    # test_parse()

    # Example Usage
    code1 = """
class MyClass:
    def existing_method(self):
        pass
"""

    code2 = """
class MyClass:
    def existing_method(self):
        pass

    async def new_method(self):
        pass
"""

    print(is_just_new_function(code1, code2))  # Output: True
