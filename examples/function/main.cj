import std.convert.*

main(args: Array<String>): Int64 {
    let index = Int64.parse(args[0])
    let size = Int64.parse(args[1])
    println(boundedIndex(index, size))
    return 0
}
