"""Fail-closed credential ACLs, without shelling out or reading file contents."""
from __future__ import annotations

import os
from pathlib import Path


def protect_private_path(path: Path) -> None:
    if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
        raise OSError('Credential paths must not be symbolic links')
    if os.name != 'nt':
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    api.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileSecurityW.restype = wintypes.BOOL
    api.GetSecurityDescriptorOwner.argtypes = [pointer, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.BOOL)]
    api.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    api.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.DWORD)]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer]
    api.SetFileSecurityW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    needed = wintypes.DWORD()
    api.GetFileSecurityW(str(path), 1, None, 0, ctypes.byref(needed))
    if not needed.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(needed.value)
    owner = pointer()
    defaulted = wintypes.BOOL()
    owner_text = wintypes.LPWSTR()
    private_descriptor = pointer()
    if not api.GetFileSecurityW(str(path), 1, descriptor, needed, ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.ConvertSidToStringSidW(owner, ctypes.byref(owner_text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # Keep the existing owner (not the SYSTEM service identity). Children
        # inherit only these grants; no Users/Everyone/inherited broad ACEs.
        inheritance = 'OICI' if path.is_dir() else ''
        sddl = 'D:P' + ''.join(f'(A;{inheritance};FA;;;{sid})' for sid in {'SY', 'BA', str(owner_text.value)})
        if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(private_descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not api.SetFileSecurityW(str(path), 4 | 0x80000000, private_descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(ctypes.cast(owner_text, pointer))
        if private_descriptor:
            kernel.LocalFree(private_descriptor)
