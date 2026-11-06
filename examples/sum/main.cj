import std.convert.*

func inclusiveSum(n: Int64): Int64 {
    var total: Int64 = 0
    for (i in 1..n) {
        total += i
    }
    return total
}

main(args: Array<String>): Int64 {
    let n = Int64.parse(args[0])
    let result = inclusiveSum(n)
    println(result)
    return 0
}
