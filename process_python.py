from __future__ import annotations

import sys
import json
import enum
import textwrap
import pathlib
import os

stdin_lines = sys.stdin.readlines()
stdin = "".join(stdin_lines)
module = sys.argv[1]
data = json.loads(stdin)

header = ""
out = ""

# types that contain a circular reference
circular_referenced_types = [ "message_structure" ]

def pascal(k: str) -> str:
    return "".join([n.capitalize() for n in k.split("_")])

def is_type(name: str) -> bool:
    if name.endswith("structure"):
        return True
    elif name.endswith("enum"):
        return False
    else:
        print(f"Not sure whether {name} is a type or an enum.", file=sys.stderr)

def fix_type(t: str) -> tuple[list[str], str]:
    # returns (requires, new_name)
    if t == "string":
        return ([], "str")
    elif t == "snowflake":
        return (["base"], "Snowflake")
    elif t == "integer":
        return ([], "int")
    elif t in ["partial_voice_state_structure", "partial_presence_update_structure", "unavailable_guild_structure"]:
        return (["typing"], "t.Dict[str, object]")
    elif t == "two_integers_(current_size,_max_size)_structure":
        # wtf
        return (["typing"], "t.Tuple[int, int]")
    # slash commands are super non-standard docs, TODO
    #elif t in ["message_interaction_structure", "message_component_structure"]:
    #    return fix_type("map")
    elif t.endswith("_structure"):
        nt = pascal(t[:-len("_structure")])
        return ([(t, nt)], nt)
    elif t.endswith("_enum"):
        nt = pascal(t[:-len("_enum")])
        return ([(t, nt)], nt)
    elif t == "map":
        return (["typing"], "t.Dict[str, object]")
    elif t == "boolean":
        return ([], "bool")
    elif t.startswith("array"):
        _, unwrapped = t.strip(">").split("<")
        req, typ = fix_type(unwrapped)
        reqs = ["typing"]
        reqs.extend(req)
        return (reqs, f"t.List[{typ}]")
    elif t == "null":
        # seriously???
        return ([], "None")
    elif t == "timestamp":
        return (["datetime"], "dt.datetime")
    elif "|" in t:
        l, r = t.split(" | ")
        l, r = l.strip(), r.strip()
        l_req, l_type = fix_type(l)
        r_req, r_type = fix_type(r)

        reqs = [req for req in l_req + r_req]

        return (reqs + ["typing"], f"t.Union[{l_type}, {r_type}]")
    elif t == "any":
        return ([], "object")
    elif t == "audit_log_change_key":
        return ([], "AuditLogChangeKey")
    else:
        raise Exception(f"unhandled {t}")

def build_type(v: dict[str, object]) -> tuple[list[str], str]:
    res = []
    bottom = []
    imports = []

    for k, v in v.items():
        type_ = v["type"]
        description = v["desc"]
        optional = v["optional"]
        nullable = v["nullable"]

        required, type_ = fix_type(type_)

        if nullable:
            type_ = f"t.Optional[{type_}]"
            imports.append("typing")

        if optional:
            type_ = f"Unknownish[{type_}]"
            imports.append("base")

        imports.extend(required)
  
        if optional:
            bottom.append(f"#: {description}")
            bottom.append(f"{k}: {type_} = UNKNOWN")
        else:
            res.append(f"#: {description}")
            res.append(f"{k}: {type_}")

    return imports, "\n".join(res) +"\n" + "\n".join(bottom)

def build_enum(v: dict[str, object]) -> str:
    res = []

    for k, v in v.items():
        if isinstance(v, dict):
            val = v["value"]

            assert not v.get("optional") and not v.get("nullable")

            if v.get("desc"):
                res.append(f"#: {v['desc']}")
        else:
            val = v

        if "<<" in k:
            k = k.strip(")").split("(")[0]
            k = eval(k.strip())

        if "<<" in val:
            val = val.strip(")").split("(")[0]
            val = eval(val.strip())

        if isinstance(k, str):
            if k.isnumeric():
                k = int(k)

        if isinstance(k, int):
            k, val = val, k

        if isinstance(val, str):
            if val.isnumeric():
                val = int(val)
            else:
                val = f'"{val}"'

        if isinstance(k, str):
            k = k.replace(" ", "_").upper()

        res.append(f"{k} = {val}")

    return "\n".join(res)

def resolve_module(entry: str) -> str:
    if entry in circular_referenced_types:
        return entry[:-len("_structure")]

    output_folder = pathlib.Path(os.path.dirname(os.path.realpath(__file__))) / "out"

    for file in output_folder.glob("*.json"):
        with open(file) as f:
            contents = json.load(f)

            if entry in contents.keys():
                return str(file).split("/")[-1].split("\\")[-1].split(".")[0]

    raise Exception(f"module for {entry} not found.")

for k, v in data.items():
    if k == "audit_log_change_key":
        if "enum" not in header:
            # should this use an IntEnum / IntFlag?
            header += "from enum import Enum\n"

        # TODO: should this somehow be an ADT for `type` to work?
        res = []
        for k, v in v.items():
            res.append(f'#: a "{v["object_changed"]}" just changed. the values are of type {v["type"]}')
            res.append(f'#: description: {v["desc"]}')
            res.append(f"{k.upper().strip('$')} = '{k}'")

        out += "class AuditLogChangeKey(Enum):\n"
        out += textwrap.indent("\n".join(res), " "*4) + "\n"

    elif is_type(k):
        if k == "identify_connection_properties_structure":
            # bad keys ($os, etc.)
            continue

        if k == "audit_log_change_structure":
            v["key"]["type"] = "audit_log_change_key"

        if k in circular_referenced_types:
            old_out, old_header = out, header
            out, header = "", ""

        if "dataclass" not in header:
            header += "from dataclasses import dataclass\n"

        k = k[:-len("_structure")]
        # it's a type!!!
        out += "@dataclass()\n"
        out += f"class {pascal(k)}:\n"

        requires, class_content = build_type(v)

        out += textwrap.indent(class_content, " "*4) + "\n"

        if "typing" in requires and "typing" not in header:
            header += "import typing as t\n"

        if "datetime" in requires and "datetime" not in header:
            header += "import datetime as dt\n"

        if "base" in requires and "base" not in header:
            header += "from .base import (Snowflake, UNKNOWN, Unknownish)\n"

        others = [req for req in requires if req not in ["typing", "datetime", "base"]]

        for mod, why in others:
            mod_name = resolve_module(mod)
            # isort should combine the imports, sorry.
            if (k + "_structure") in circular_referenced_types:
                if k != mod_name:
                    header += f"from .{mod_name} import {why}\n"
            elif mod_name != module:
                header += f"from .{mod_name} import {why}\n"

        if (k + "_structure") in circular_referenced_types:
            output_folder = pathlib.Path(os.path.dirname(os.path.realpath(__file__))) / "out" / "python"
            with open(output_folder / (k + ".py"), "w") as f:
                f.write("from __future__ import annotations\n" + header + "\n" + out)

            out = old_out
            header = old_header
    else:
        if "enum" not in header:
            # should this use an IntEnum / IntFlag?
            header += "from enum import Enum\n"

        out += f"class {pascal(k[:-len('_enum')])}(Enum):\n"

        out += textwrap.indent(build_enum(v), " "*4) + "\n"

# future annotations will allow not ordering class definitions
# (simplifying code generation)
print("from __future__ import annotations\n" + header + "\n" + out)
