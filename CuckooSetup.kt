import kotlin.math.abs
import kotlin.random.Random

open class CuckooSetup<K, V>(var tableSize: Int) {

    // Maximum number of times to loop when inserting before giving up
    val MAX_LOOP = 100

    // Seed for random number generator used when getting hash code
    var seed = 0

    data class Entry<K, V>(val key: K, var value: V)

    // Very minimal hash code approach that enables the functionality of
    // creating new hash functions as we need them. The literature on cuckoo
    // hashing suggests that there are approaches that will improve performance
    // further, but for purposes of this assignment it will be plenty good
    // enough.
    //
    // tableNum is either 0 or 1, representing one of the two hash tables.
    fun cuckooHashCode(tableNum: Int, key: K): Int {
        val multiplier = Random(2 * seed + tableNum).nextInt()
        val newHashCode = multiplier * key.hashCode()
        // newHashCode might be negative
        return abs(newHashCode)
    }


    // The two hash tables.
    var tables =
            listOf(
                    Array<Entry<K, V>?>(tableSize) { null },
                    Array<Entry<K, V>?>(tableSize) { null },
            )

    // For debugging purposes, display both tables
    fun display() {
        for (row in 0 ..< tableSize) {
            print(String.format("%2d ", row))
            print(String.format("%40s", "${tables[0][row]}"))
            print(" ")
            print(String.format("%40s", "${tables[1][row]}"))
            println()
        }
    }
}
