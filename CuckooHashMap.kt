/*
 * Author: Joey Baker
 * 
 * Collaboration statement: I consulted the prefect, notes, and class textbook on this assignment.
 */

import kotlin.math.abs
import kotlin.random.Random

class CuckooHashMap<K, V>(tableSize: Int) : CuckooSetup<K, V>(tableSize) {
    val tableOne: Int = 0
    val tableTwo: Int = 1
    val twice: Int = 2
    // adds or updates the key/value pair in the given table, 
    // and returns the entry that was evicted, or null if no 
    // entry was previously stored in that location
    private fun addOrUpdate(key: K, value: V, tableNum: Int): Entry<K, V>? {
        val keyValue = Entry<K, V>(key, value)
        val code = getCode(key, tableNum)
        val oldValue = tables[tableNum][code]
        tables[tableNum][code] = keyValue
        return oldValue
    }
    // tests to see if there is a key in the spot that it should be
    private fun testKey(key: K, tableNo: Int): K? {
        val code = getCode(key, tableNo)
        val loc = tables[tableNo][code]?.key
        return loc
    }
    //returns the value given a key
    private fun getValue(key: K, tableNo: Int) : V {
        val code = getCode(key, tableNo)
        val valu = tables[tableNo][code]!!.value
        return valu
    }
    //function to get the code for where the key will go
    private fun getCode(key: K, tableNo: Int) : Int {
        return (cuckooHashCode(tableNo, key) % tableSize)
    }
    // when the tables need rehashing, this function saves the old tables to a long array so that
    // they can be rehashed
    private fun saveOldTable() : Array<Entry<K, V>?> {
        val oldTable = Array<Entry<K, V>?>(tableSize*twice) { null }
        for (j in 0..<tableSize*twice) {
            var tableNo: Int = j/tableSize //if j<20, uses the first table, if j>20, uses the second table
            var position: Int = j%tableSize //if j>20, then this sets it back to within the index of tables
            oldTable[j] = tables[tableNo][position]
            tables[tableNo][position] = null
        }
        return oldTable
    }

    // Look up a key, and get the corresponding value. Try both tables as
    // needed. Returns null if not found.
    fun get(key: K): V? {
        val codeOne = testKey(key, tableOne)
        if (codeOne == key) { //checks to see if it's the right key
            val valu = getValue(key, tableOne)
            return valu
        } else {
            val codeTwo = testKey(key, tableTwo)
            if (codeTwo == key) {
                val valu = getValue(key, tableTwo)
                return valu
            } else {
                return null
            }
        }
    }

    // Adds a key and a value to one of the hash tables.
    fun set(key: K, value: V) {
        var tableNo: Int = 0
        var result: Entry<K, V>? = addOrUpdate(key, value, tableNo)
        var counter: Int = 0
        while (result != null && counter < MAX_LOOP) {
            tableNo++
            counter++
            result = addOrUpdate(result.key, result.value, tableNo%2)
        }
        if (counter == MAX_LOOP) {
            val kicked = result //keeps the value that was floating about
            val oldTable = saveOldTable()
            seed++
            for (entry in oldTable) {
                if (entry != null) {
                    this.set(entry!!.key, entry!!.value)
                }
            }
            this.set(kicked!!.key, kicked!!.value)
            }
        }
}

/*
* Reflection: This assignment went good. I forgot we needed to do rehashing at first, but 
after I figured it out, it went quickly. I spent about five hours on the assignment.
*/


/*  
* AI Log: I used AI overview to look up how to do division in kotlin 
(the equivalent of python's //, specifically)
*/