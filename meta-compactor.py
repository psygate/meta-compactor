import os
import sys
import random
import time
import typing
import hashlib
import pprint
import copy
import unittest
import pathlib
from functools import reduce
from pathlib import PurePath, Path
import shutil
import logging

__LOGGER__ = None
__SIGNATURE__ = b'METACOMPACTOR-REPLACEMENT-FILE'


class Pathable:
  def __init__(self, name, parent=None):
    assert parent is None or isinstance(parent, Pathable), f"Parent type unknown: {type(parent)}"
    assert name is not None, "Name cannot be none."

    self._parent = parent
    self._name = name

  @property
  def path(self):
    if self._parent:
      return PurePath(self._parent.path, self._name)
    else:
      return PurePath(self._name)

  @property
  def parent(self):
    return self._parent

  @property
  def name(self):
    return self._name

  def asdict(self):
    return {
      "name": self.name,
      "path": self.path,
    }

  def __repr__(self):
    return f"{self.path}"


class Directory(Pathable):
  def __init__(self, name, parent=None):
    super(Directory, self).__init__(name, parent)
    self.children = []

  def prune_child(self, child):
    self.children = [x for x in self.children if x is not child]

  def replace_child(self, child, replacement):
    self.prune_child(child)
    self.children.append(FileLink(child, replacement))

  def prune_children(self, checksum_values={}):
    for child in self.file_children:
      if child.checksum in checksum_values:
        self.replace_child(child, checksum_values[child.checksum])
      else:
        checksum_values[child.checksum] = child

    for child in self.directory_children:
      child.prune_children(checksum_values)

  def apply_changes(self):
    for child in self.children:
      if isinstance(child, FileLink):
        __LOGGER__.debug(f"Replacing: {child}")

        with open(child.path, "rb") as f:
          signature = f.read(len(__SIGNATURE__))

        if signature != __SIGNATURE__:
          os.remove(child.path)
          with open(child.path, "wb") as f:
            f.write(__SIGNATURE__)

      elif isinstance(child, File):
        __LOGGER__.debug(f"Ignoring: {child}")
      elif isinstance(child, Directory):
        __LOGGER__.debug(f"Descending: {child}")
        child.apply_changes()
      else:
        raise NotImplementedError()

  def restore(self):
    for child in [x for x in self.children if isinstance(x, FileLink)]:
      __LOGGER__.debug(f"Restoring: {child} with {child.replacement}")
      with open(child.path, "wb") as left:
        with open(child.replacement, "rb") as right:
          while True:
            data = right.read(4096)

            if not data:
              break
            else:
              left.write(data)

    for child in [x for x in self.children if isinstance(x, Directory)]:
      __LOGGER__.debug(f"Descending for restoration: {child}")
      child.restore()

  @property
  def file_children(self):
    return [x for x in self.children if isinstance(x, File)]

  @property
  def directory_children(self):
    return [x for x in self.children if isinstance(x, Directory)]

  def asdict(self):
    return {
      **super(Directory, self).asdict(),
      "type": "directory",
      "children": [x.asdict() for x in self.children]
    }


class FileLink(Pathable):
  def __init__(self, original, replacement):
    super(FileLink, self).__init__(original.name, original.parent)

    assert original.checksum == replacement.checksum

    self.replacement = replacement.path

    self._checksum = original.checksum

  @property
  def checksum(self):
    return self._checksum

  def asdict(self):
    return {
      **super(FileLink, self).asdict(),
      "type": "file link",
      "replacement": self.replacement,
      "checksum": self.checksum.hex(),
    }


class File(Pathable):
  def __init__(self, name, parent=None, eager_checksum=False):
    super(File, self).__init__(name, parent)

    self._checksum = None

    if eager_checksum:
      self.checksum

  @property
  def checksum(self):
    if not self._checksum:
      sha = hashlib.sha256()
      with open(self.path, "rb") as f:
        while True:
          read_bytes = f.read(4096)
          if not read_bytes:
            break
          else:
            sha.update(read_bytes)

      self._checksum = sha.digest()

    return self._checksum

  def asdict(self):
    return {
      **super(File, self).asdict(),
      "type": "file",
      "checksum": self.checksum.hex(),
    }


def index_directory(root: pathlib.Path, parent=None):
  if root.is_file():
    return File(root.name, parent)
  elif root.is_dir():
    directory = Directory(root.name, parent)
    children = [index_directory(sub, directory) for sub in root.iterdir()]
    directory.children = children
    return directory
  else:
    raise NotImplementedError(f"Cannot determine type of {root}")


def meta_compactor_main(dirname):
  tree = index_directory(pathlib.Path(dirname))
  pprint.pprint(tree.asdict(), indent=2)
  tree.prune_children()
  pprint.pprint(tree.asdict(), indent=2)
  # reduced = reduce_tree(tree)

  # tree = list()
  # for dirname in directories:
  #   dir = index_directory(dirname)
  #   tree.append(dir)
  #
  #   pprint.pprint(index_directory(dirname))
  #
  # tree = reduce_directory_tree(tree)


def compare_files(_left, _right):
  left = Path(_left)
  right = Path(_right)

  with open(left, "rb") as l:
    with open(right, "rb") as r:
      while True:
        lr = l.read(4096)
        rr = r.read(4096)

        if not lr and not rr:
          break

        if lr != rr:
          return False

  return True


def compare_directories(_left, _right):
  left = Path(_left)
  right = Path(_right)

  if sum(1 for x in left.iterdir()) != sum(1 for x in right.iterdir()):
    __LOGGER__.debug(f"{left} doesn't have the same children number as {right}")
    return False

  for lchild, rchild in zip(left.iterdir(), right.iterdir()):
    if lchild.is_file() and rchild.is_file():
      comparison = compare_files(lchild, rchild)
      __LOGGER__.debug(f"Comparing: {lchild} {rchild} => {comparison}")
      if not comparison:
        return False
    elif lchild.is_dir() and rchild.is_dir():
      if not compare_directories(lchild, rchild):
        return False
    else:
      __LOGGER__.debug(f"{left} isn't the same as {right}. File: {left.is_file()} vs {right.is_file()} "
                       f"Directory: {left.is_dir()} vs {right.is_dir()}")
      return False

  return True


def test_meta_compactor(dirname):
  tgtdir = f"{dirname}_copy"
  shutil.rmtree(tgtdir)
  shutil.copytree(dirname, tgtdir)

  assert compare_directories(dirname, tgtdir)

  tree = index_directory(pathlib.Path(tgtdir))
  __LOGGER__.debug(pprint.pformat(tree.asdict(), indent=2))
  tree.prune_children()

  assert compare_directories(dirname, tgtdir)

  __LOGGER__.debug(pprint.pformat(tree.asdict(), indent=2))
  tree.apply_changes()

  tree.restore()
  assert compare_directories(dirname, tgtdir)
  shutil.rmtree(tgtdir)


def make_test_data():
  names = ["a", "b", "c", "d", "e", "f"]
  basedir = "testdir"

  def makedir(basedir, name, files, depth=0, random=random.Random(0x1337abcd)):
    path = os.path.join(basedir, name)
    os.makedirs(path, exist_ok=True)

    for f in [x for x in names if random.randint(0, 1) == 1]:
      with open(os.path.join(path, f"{f}.bin"), "wb") as o:
        data = random.choice(files)
        o.write(data)

    if depth < 3:
      for name in names:
        makedir(path, name, files, depth + 1, random)

  files = [bytes(range(128)), bytes(range(64)), bytes(range(32)), bytes(range(16)),
           bytes(x % 256 for x in range(4096)), ]

  for name in names:
    makedir(basedir, name, files)


if __name__ == '__main__':
  logging.basicConfig(level=logging.DEBUG)
  __LOGGER__ = logging.getLogger("meta-compactor")
  __LOGGER__.setLevel(logging.DEBUG)
  test_meta_compactor("testdir")
