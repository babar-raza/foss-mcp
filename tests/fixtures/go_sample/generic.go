package pdf

// Pair holds two values.
type Pair[T any] struct {
	// First is the first value.
	First T
}

// Swap swaps the pair.
func (p *Pair[T]) Swap() {}
