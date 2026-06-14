#!/usr/bin/env python3
"""map_codebase — Project Utah dynamic codebase mapping tool.

Iterates over every python file in the utah/ codebase, parses its AST structure,
and extracts details for all classes, methods, functions, and module constants.
Outputs a complete, serialized JSON structure to ~/.utah/run/codebase_map.json
and prints a summary of the mapped items.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

# Resolve paths
ROOT = Path(__file__).resolve().parent.parent
UTAH_DIR = ROOT / "utah"
OUTPUT_DIR = Path(os.environ.get("UTAH_HOME", os.path.expanduser("~/.utah"))) / "run"
OUTPUT_FILE = OUTPUT_DIR / "codebase_map.json"


def parse_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Reconstruct function arguments into a signature string."""
    args = []
    
    # Positional-only arguments
    if hasattr(node.args, "posonlyargs"):
        for arg in node.args.posonlyargs:
            annotation = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
            args.append(f"{arg.arg}{annotation}")
            
    # Positional arguments
    for arg in node.args.args:
        annotation = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        args.append(f"{arg.arg}{annotation}")
        
    # vararg (*args)
    if node.args.vararg:
        annotation = f": {ast.unparse(node.args.vararg.annotation)}" if node.args.vararg.annotation else ""
        args.append(f"*{node.args.vararg.arg}{annotation}")
        
    # Kwonly arguments
    for arg in node.args.kwonlyargs:
        annotation = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        args.append(f"{arg.arg}{annotation}")
        
    # kwarg (**kwargs)
    if node.args.kwarg:
        annotation = f": {ast.unparse(node.args.kwarg.annotation)}" if node.args.kwarg.annotation else ""
        args.append(f"**{node.args.kwarg.arg}{annotation}")
        
    sig = ", ".join(args)
    ret = ""
    if node.returns:
        ret = f" -> {ast.unparse(node.returns)}"
    return f"def {node.name}({sig}){ret}"


def map_file(p: Path) -> dict:
    """Parse a single python file and return its structural details."""
    rel_path = str(p.relative_to(ROOT))
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except Exception as exc:
        return {
            "file": rel_path,
            "error": f"Failed to parse: {exc}",
            "classes": [],
            "functions": [],
            "constants": []
        }

    doc = ast.get_docstring(tree) or ""
    constants = []
    classes = []
    functions = []

    for node in tree.body:
        # Module-level assignments / Constants
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            try:
                tgt = node.targets[0] if isinstance(node, ast.Assign) and node.targets else getattr(node, "target", None)
                if isinstance(tgt, ast.Name):
                    val = ""
                    if isinstance(node, ast.Assign) and node.value:
                        val = ast.unparse(node.value)
                    elif isinstance(node, ast.AnnAssign) and node.value:
                        val = ast.unparse(node.value)
                    
                    constants.append({
                        "name": tgt.id,
                        "line": node.lineno,
                        "value": val
                    })
            except Exception:
                pass

        # Module-level Functions
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                "name": node.name,
                "async": isinstance(node, ast.AsyncFunctionDef),
                "line": node.lineno,
                "signature": parse_function_signature(node),
                "docstring": ast.get_docstring(node) or ""
            })

        # Classes
        elif isinstance(node, ast.ClassDef):
            class_methods = []
            for sub_node in node.body:
                if isinstance(sub_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_methods.append({
                        "name": sub_node.name,
                        "async": isinstance(sub_node, ast.AsyncFunctionDef),
                        "line": sub_node.lineno,
                        "signature": parse_function_signature(sub_node),
                        "docstring": ast.get_docstring(sub_node) or ""
                    })

            classes.append({
                "name": node.name,
                "line": node.lineno,
                "bases": [ast.unparse(base) for base in node.bases],
                "docstring": ast.get_docstring(node) or "",
                "methods": class_methods
            })

    return {
        "file": rel_path,
        "docstring": doc,
        "classes": classes,
        "functions": functions,
        "constants": constants
    }


def main() -> int:
    if not UTAH_DIR.exists() or not UTAH_DIR.is_dir():
        sys.stderr.write(f"utah/ package directory not found at: {UTAH_DIR}\n")
        return 1

    mapped_files = []
    py_files = sorted(p for p in UTAH_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    
    total_classes = 0
    total_functions = 0
    total_constants = 0

    print(f"Scanning Project Utah source files under {UTAH_DIR}...")
    for p in py_files:
        meta = map_file(p)
        mapped_files.append(meta)
        total_classes += len(meta.get("classes", []))
        total_functions += len(meta.get("functions", []))
        total_constants += len(meta.get("constants", []))

    # Write JSON output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "project": "Utah",
            "files_count": len(py_files),
            "total_classes": total_classes,
            "total_functions": total_functions,
            "total_constants": total_constants,
            "files": mapped_files
        }, f, indent=2)

    # Print live CLI summary report
    print(f"\n==========================================")
    print(f"Project Utah Codebase Map Summary (Live Source)")
    print(f"==========================================")
    print(f"Total Python Files Mapped: {len(py_files)}")
    print(f"Total Classes Extracted  : {total_classes}")
    print(f"Total Functions Mapped   : {total_functions}")
    print(f"Total Constants Mapped   : {total_constants}")
    print(f"Output serialized to: {OUTPUT_FILE}\n")

    # List files and their main declarations
    print("Files Index:")
    for f in mapped_files:
        items = []
        if f.get("classes"):
            items.append(f"{len(f['classes'])} classes")
        if f.get("functions"):
            items.append(f"{len(f['functions'])} functions")
        if f.get("constants"):
            items.append(f"{len(f['constants'])} constants")
        
        items_str = ", ".join(items) if items else "no items"
        print(f"  * {f['file']} ({items_str})")
        
        # Display class names
        for cls in f.get("classes", []):
            print(f"      - Class {cls['name']}")
        # Display top-level function names
        for fn in f.get("functions", []):
            print(f"      - Func  {fn['name']}()")

    return 0


if __name__ == "__main__":
    sys.exit(main())
