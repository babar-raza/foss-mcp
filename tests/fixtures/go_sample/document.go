// Package pdf reads and writes PDF documents.
package pdf

import "io"

// HAlign is the horizontal alignment of text.
type HAlign int

// Horizontal alignments.
const (
	// HAlignLeft aligns text to the left edge.
	HAlignLeft HAlign = iota
	// HAlignCenter centres text.
	HAlignCenter
	HAlignRight
)

// SaveFormat selects the output format.
type SaveFormat string

const (
	SaveFormatPDF  SaveFormat = "pdf"
	SaveFormatHTML SaveFormat = "html"
)

// Flags is a bit set.
type Flags uint

const (
	FlagA Flags = 1 << iota
	FlagB
	flagHidden
)

// Document is an open PDF document.
type Document struct {
	// Title is the document title.
	Title string
	pages []int
}

// Open opens the PDF at path. It returns an error if the file cannot be read.
func Open(path string) (*Document, error) { return nil, nil }

// OpenWithPassword opens an encrypted PDF at path using password.
func OpenWithPassword(path, password string) (*Document, error) { return nil, nil }

// NewDocument creates an empty document.
func NewDocument() *Document { return &Document{} }

// NewDocumentFromFormat creates an empty document for format.
func NewDocumentFromFormat(format SaveFormat) *Document { return &Document{} }

// Save writes the document to w, for example a file or a buffer.
func (d *Document) Save(w io.Writer, format SaveFormat) error { return nil }

func (d Document) pageCount() int { return 0 }

type helper struct{}

// Flatten flattens helper.
func (h *helper) Flatten() {}

func unexported() {}
