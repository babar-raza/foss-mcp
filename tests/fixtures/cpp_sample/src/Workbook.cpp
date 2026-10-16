#include "cells/Workbook.h"
namespace Aspose::Cells {
namespace {
double GetElapsedMinutes(double d) { return d / 60; }
}
static bool IsNumericFormatChar(char c) noexcept { return c == '0'; }
std::string EscapeTextContent(const std::string& s) { return s; }
Workbook::Workbook() {}
void Workbook::Save(std::vector<std::uint8_t>& stream, SaveFormat format) const {}
bool IsLeapYear(int year) { return year % 4 == 0; }
}
