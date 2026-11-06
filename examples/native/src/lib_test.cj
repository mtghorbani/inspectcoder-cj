package bounds
import std.unittest.*
import std.unittest.testmacro.*

@Test
public class Bounds {
    @TestCase
    func high() { @Expect(boundedIndex(5, 3), 2) }
    @TestCase
    func low() { @Expect(boundedIndex(-1, 3), 0) }
}
