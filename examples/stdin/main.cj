import std.console.*
import std.convert.*

main(): Int64 {
    let n = Int64.parse(Console.stdIn.readln().getOrThrow())
    println(n * 2)
    return 0
}
