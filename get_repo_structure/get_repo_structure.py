import argparse
import ast
import json
import os
import shutil
import subprocess
import uuid

import pandas as pd
from tqdm import tqdm

repo_to_top_folder = {
    "django/django": "django",
    "sphinx-doc/sphinx": "sphinx",
    "scikit-learn/scikit-learn": "scikit-learn",
    "sympy/sympy": "sympy",
    "pytest-dev/pytest": "pytest",
    "matplotlib/matplotlib": "matplotlib",
    "astropy/astropy": "astropy",
    "pydata/xarray": "xarray",
    "mwaskom/seaborn": "seaborn",
    "psf/requests": "requests",
    "pylint-dev/pylint": "pylint",
    "pallets/flask": "flask",
}


def get_top_folder(repo_name: str) -> str:
    """Return the local checkout folder for a repository name.

    The original implementation only supported the twelve SWE-bench
    repositories listed above.  Deriving the last path component keeps the
    function usable for forks and custom repositories.
    """
    return repo_to_top_folder.get(repo_name, repo_name.rstrip("/").split("/")[-1])


def checkout_commit(repo_path, commit_id):
    """Checkout the specified commit in the given local git repository.
    :param repo_path: Path to the local git repository
    :param commit_id: Commit ID to checkout
    :return: None
    """
    print(f"Checking out commit {commit_id} in repository at {repo_path}...")
    result = subprocess.run(
        ["git", "-C", repo_path, "checkout", commit_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to checkout {commit_id} in {repo_path}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    print("Commit checked out successfully.")


def clone_repo(repo_name, repo_playground):
    destination = os.path.join(repo_playground, get_top_folder(repo_name))
    print(
        f"Cloning repository from https://github.com/{repo_name}.git to {destination}..."
    )
    result = subprocess.run(
        ["git", "clone", f"https://github.com/{repo_name}.git", destination],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to clone {repo_name}: {result.stderr.strip() or result.stdout.strip()}"
        )
    print("Repository cloned successfully.")
    return destination


def get_project_structure_from_scratch(
    repo_name, commit_id, instance_id, repo_playground
):

    # Generate a temperary folder and add uuid to avoid collision
    repo_playground = os.path.join(repo_playground, str(uuid.uuid4()))

    # assert playground doesn't exist
    assert not os.path.exists(repo_playground), f"{repo_playground} already exists"

    # create playground
    os.makedirs(repo_playground)

    try:
        repo_path = clone_repo(repo_name, repo_playground)
        checkout_commit(repo_path, commit_id)
        structure = create_structure(repo_path)
        return {
            "repo": repo_name,
            "base_commit": commit_id,
            "structure": structure,
            "instance_id": instance_id,
        }
    finally:
        # rmtree works on Windows as well as POSIX; the old `rm -rf` command
        # failed outright on Windows.
        shutil.rmtree(repo_playground, ignore_errors=True)


def parse_python_file(file_path, file_content=None):
    """Parse a Python file to extract class and function definitions with their line numbers.
    :param file_path: Path to the Python file.
    :return: Class names, function names, and file contents
    """
    if file_content is None:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as file:
                file_content = file.read()
                parsed_data = ast.parse(file_content)
        except Exception as e:  # Catch all types of exceptions
            print(f"Error in file {file_path}: {e}")
            return [], [], ""
    else:
        try:
            parsed_data = ast.parse(file_content)
        except Exception as e:  # Catch all types of exceptions
            print(f"Error in file {file_path}: {e}")
            return [], [], ""

    class_info = []
    function_names = []
    class_methods = set()

    for node in ast.walk(parsed_data):
        if isinstance(node, ast.ClassDef):
            methods = []
            for n in node.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(
                        {
                            "name": n.name,
                            "start_line": n.lineno,
                            "end_line": n.end_lineno,
                            "text": file_content.splitlines()[
                                n.lineno - 1 : n.end_lineno
                            ],
                        }
                    )
                    class_methods.add(n.name)
            class_info.append(
                {
                    "name": node.name,
                    "start_line": node.lineno,
                    "end_line": node.end_lineno,
                    "text": file_content.splitlines()[
                        node.lineno - 1 : node.end_lineno
                    ],
                    "methods": methods,
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in class_methods:
                function_names.append(
                    {
                        "name": node.name,
                        "start_line": node.lineno,
                        "end_line": node.end_lineno,
                        "text": file_content.splitlines()[
                            node.lineno - 1 : node.end_lineno
                        ],
                    }
                )

    return class_info, function_names, file_content.splitlines()


def create_structure(directory_path):
    """Create the structure of the repository directory by parsing Python files.
    :param directory_path: Path to the repository directory.
    :return: A dictionary representing the structure.
    """
    structure = {}
    repo_name = os.path.basename(directory_path)
    skip_dirs = {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        ".mypy_cache",
        ".pytest_cache",
    }

    for root, dirs, files in os.walk(directory_path):
        dirs[:] = [directory for directory in dirs if directory not in skip_dirs]
        relative_root = os.path.relpath(root, directory_path)
        if relative_root == ".":
            relative_root = repo_name
        curr_struct = structure
        for part in relative_root.split(os.sep):
            if part not in curr_struct:
                curr_struct[part] = {}
            curr_struct = curr_struct[part]
        for file_name in files:
            if file_name.endswith(".py"):
                file_path = os.path.join(root, file_name)
                class_info, function_names, file_lines = parse_python_file(file_path)
                curr_struct[file_name] = {
                    "classes": class_info,
                    "functions": function_names,
                    "text": file_lines,
                }
            else:
                curr_struct[file_name] = {}

    return structure
