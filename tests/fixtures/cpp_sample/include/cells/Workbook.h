#pragma once
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace Aspose::Cells {

enum class SaveFormat { Xlsx, Csv };

class WorksheetCollection;

/// <summary>
/// Represents an Excel workbook. Entry point of the library.
/// </summary>
class Workbook {
public:
    /// <summary>Creates an empty workbook.</summary>
    Workbook();
    /// <summary>Opens the workbook stored at the given path.</summary>
    explicit Workbook(std::string_view path);
    Workbook(const std::vector<std::uint8_t>& data, int flags = 0);
    Workbook(const Workbook&) = delete;
    Workbook& operator=(const Workbook&) = delete;
    Workbook(Workbook&&) noexcept = default;
    virtual ~Workbook();

    /// <summary>Saves the workbook to a stream.</summary>
    void Save(std::vector<std::uint8_t>& stream, SaveFormat format) const;
    /// <summary>Gets the worksheets.</summary>
    WorksheetCollection& GetWorksheets();
    const WorksheetCollection& GetWorksheets() const;
    static Workbook Open(const std::string& path, const char* password = nullptr) noexcept;
    bool operator==(const Workbook& other) const;
    Workbook& operator[](int index);
    int Count() const noexcept { return count_; }
    std::string name;

private:
    void Helper();
    int count_ = 0;
};

/// Returns true for a leap year.
bool IsLeapYear(int year);
std::string ToUpper(const std::string& s);

}  // namespace Aspose::Cells
