#pragma once
#include <string>
namespace Aspose::Cells {
/// Converts a value to text.
std::string ToString(int value);
std::string ToString(double value);
inline int Clamp(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
static int FileLocal(int v) { return v; }
namespace {
int AnonymousHelper(int v) { return v; }
}
}
