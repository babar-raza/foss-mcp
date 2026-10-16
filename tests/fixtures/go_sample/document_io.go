package pdf

// Close releases the document.
func (d *Document) Close() error { return nil }

// Run runs a job of a type this package does not declare.
func (m *Missing) Run() {}
