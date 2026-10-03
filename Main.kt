fun main() {
    val map = CuckooHashMap<Int, String>(20)
    val max = 20
    for (key in 0..max step 2) {
        map.set(key, "k" + key)
    }
    for (key in 0..max step 2) {
        println("k$key, ${map.get(key)}")
    }
    for (key in 1..max step 2) {
        println(map.get(key))
    }
}
