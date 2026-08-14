"""
eon.package.package_store
===========================
Persistencia pura de Packages. Sin lógica de dominio.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from .models import Package


class PackageStore(ABC):
    """Persistencia pura de Packages."""

    @abstractmethod
    def create(self, package: Package) -> Package: ...

    @abstractmethod
    def get(self, package_id: str) -> Package | None: ...

    @abstractmethod
    def update(self, package: Package) -> Package: ...

    @abstractmethod
    def list(self) -> list[Package]: ...


class InMemoryPackageStore(PackageStore):
    """Implementación en memoria de PackageStore."""

    def __init__(self) -> None:
        self._packages: dict[str, Package] = {}
        self._lock = threading.Lock()

    def create(self, package: Package) -> Package:
        with self._lock:
            self._packages[package.id] = package
            return package

    def get(self, package_id: str) -> Package | None:
        with self._lock:
            return self._packages.get(package_id)

    def update(self, package: Package) -> Package:
        with self._lock:
            self._packages[package.id] = package
            return package

    def list(self) -> list[Package]:
        with self._lock:
            return list(self._packages.values())
