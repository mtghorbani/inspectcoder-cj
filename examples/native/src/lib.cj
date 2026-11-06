package bounds

public func boundedIndex(index: Int64, size: Int64): Int64 {
    if (index < 0) { return 0 }
    if (index >= size) { return size }
    return index
}
