"""Per-user, per-session Markdown artifacts. Paths cannot escape the root."""
import os
import re
import shutil

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "artifacts"))
_NAME = re.compile(r"^[\w.\- \u4e00-\u9fff]{1,60}\.md$")


def _root():
    os.makedirs(ROOT, exist_ok=True)
    return os.path.realpath(ROOT)


def _user_ok(username):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", username or ""))


def session_dir(username, session_id, create=False):
    if not _user_ok(username):
        raise ValueError("用户名无效")
    sid = int(session_id)
    if sid <= 0:
        raise ValueError("对话无效")
    root = _root()
    path = os.path.realpath(os.path.join(root, username, str(sid)))
    if not path.startswith(root + os.sep):
        raise ValueError("路径越界")
    if create:
        os.makedirs(path, exist_ok=True)
        path = os.path.realpath(path)
        if not path.startswith(root + os.sep):
            raise ValueError("路径越界")
    return path


def safe_name(name):
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    if name and not name.lower().endswith(".md"):
        name += ".md"
    if not _NAME.fullmatch(name) or ".." in name:
        raise ValueError("文件名只能包含字母、数字、中文、点、下划线和短横线，且以 .md 结尾")
    return name


def write_text(username, session_id, name, content):
    name = safe_name(name)
    content = content or ""
    if len(content) > 100_000:
        raise ValueError("报告过长")
    dest_dir = session_dir(username, session_id, create=True)
    dest = os.path.realpath(os.path.join(dest_dir, name))
    if not dest.startswith(dest_dir + os.sep):
        raise ValueError("路径越界")
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, dest)
    return {"name": name, "size": os.path.getsize(dest)}


def list_files(username, session_id):
    try:
        dest = session_dir(username, session_id, create=False)
    except ValueError:
        return []
    if not os.path.isdir(dest):
        return []
    out = []
    for fn in sorted(os.listdir(dest)):
        full = os.path.join(dest, fn)
        if not os.path.isfile(full) or ".." in fn or not fn.endswith(".md"):
            continue
        st = os.stat(full)
        out.append({"name": fn, "size": st.st_size, "mtime": st.st_mtime})
    return out


def resolve(username, session_id, name):
    name = safe_name(name)
    dest_dir = session_dir(username, session_id, create=False)
    if not os.path.isdir(dest_dir):
        raise ValueError("文件不存在")
    dest = os.path.realpath(os.path.join(dest_dir, name))
    if not dest.startswith(dest_dir + os.sep) or not os.path.isfile(dest):
        raise ValueError("文件不存在")
    return dest


def read_text(path, limit=200_000):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(limit)


def delete_file(username, session_id, name):
    os.remove(resolve(username, session_id, name))


def remove_session(username, session_id):
    try:
        dest = session_dir(username, session_id, create=False)
    except ValueError:
        return
    root = _root()
    if os.path.isdir(dest) and dest.startswith(root + os.sep):
        shutil.rmtree(dest)
