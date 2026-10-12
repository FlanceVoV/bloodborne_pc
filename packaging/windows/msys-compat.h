#pragma once

// FidelityFX SDK 1.1.4 assumes MSVC's _countof and swprintf_s names on Windows.
// MSYS2 CLANG64 uses the MinGW C runtime, which provides swprintf instead.
#if defined(_WIN32) && !defined(_MSC_VER)
#include <wchar.h>
#ifndef _countof
#define _countof(array) (sizeof(array) / sizeof((array)[0]))
#endif
#ifndef swprintf_s
#define swprintf_s swprintf
#endif
#endif

