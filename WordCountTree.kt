/*
 * Author: Joey Baker
 * 
 * Collaboration statement: I consulted my notes, teacher, prefect, and textbook on this assignment.
 */


/**
 * WordCountTree class contains word counts within a provided series of strings.
 * Each node of the WordCountTree (WCT) contains a count. The children of a node
 * are all of the characters that could come after the node's character to
 * represent a word.
 */
class WordCountTree {
    // Nobody else needs to see how we internally store nodes, so
    // we keep the data class private
    private data class Node(
        var count: Int = 0,
        val children: MutableMap<Char, Node> = mutableMapOf<Char, Node>())

    // Store a pointer to the root node in the tree
    private var root: Node = Node()
    private var curr: Node? = root
    val lengthCheck: Int = 1
    val firstIndex: Int = 0
    val secondChar: Int = 1
    val noCount: Int = 0

    /**
     * Returns a string representation of the tree.
     */
    override fun toString(): String {
        return root.toString()
    }

    /**
     * Adds 1 to the existing count for given word, 
     * or adds given word to the WordCountTree with a count of 1 
     * if it is not already present.
     * Implementation must be recursive, not iterative.
     */
    fun incrementCount(word: String) {
        curr = root
        incrementCountHelper(word, curr)
    }
    //helper to handle the recursive part of incrementcount
    private fun incrementCountHelper(word: String, current: Node?) {
        var currLoc: Node? = current
        var length = word.length
        var firstChar: Char = word[firstIndex]
        if (currLoc?.children[firstChar] == null) {
            if (length == lengthCheck) {
                currLoc?.children[firstChar] = Node()
                currLoc?.children[firstChar]?.count++
            } else {
                currLoc?.children[firstChar] = Node()
                currLoc = currLoc?.children[firstChar]
                incrementCountHelper(word.substring(secondChar,length), currLoc)
            }
        } else {
            if (length == lengthCheck) {
                currLoc?.children[firstChar]?.count++
            } else {
                currLoc = currLoc?.children[firstChar]
                incrementCountHelper(word.substring(secondChar,length), currLoc)
            }
            }  
    }


    /**
     * Returns the count of word. Returns 0 if word is not present.
     * Implementation must be recursive, not iterative.
     */
    fun getCount(word: String): Int {
        var current = root
        val count: Int = getCountHelper(word, current)
        return count
    }
    // a helper to handle the recursive part of getCount
    private fun getCountHelper(word: String, current: Node?): Int {
        var currLocal = current
        var length = word.length
        var firstChar: Char = word[firstIndex]
        if (length > lengthCheck && currLocal?.children[firstChar] != null) {
            currLocal = currLocal?.children[firstChar]
            return getCountHelper(word.substring(secondChar, length), currLocal)
        } else if (length == lengthCheck && currLocal?.children[firstChar] != null) { //if it is null then it hasn't been created before and must be 0
            return currLocal?.children[firstChar]!!.count
        }
        return noCount
    }

    /**
     * Returns true if word is stored in this WordCountTree
     * with a count greater than 0, and false otherwise.
     * Implementation must be recursive, not iterative.
     */
    fun contains(word: String): Boolean {
        var current = root
        val bool: Boolean = containsHelper(word, current)
        return bool
    }
    // a helper to contains that just handles the recursve part of contains
    private fun containsHelper(word: String, current: Node?): Boolean{
        var currLocal = current
        var length = word.length
        var firstChar: Char = word[firstIndex]
        if (length == lengthCheck) {
            if (currLocal?.children[firstChar] == null) { //if it doesnt exist, then it's 0
                return false
            } else {
                return (currLocal!!.children[firstChar]!!.count > firstIndex)
            }
        } else {
            if (currLocal?.children[firstChar] != null) {
                currLocal = currLocal?.children[firstChar]
                return containsHelper(word.substring(secondChar,length), currLocal)
            }
        }
        return false
    }

    /**
     * Returns a MutableMap of all words in WordCountTree that
     * start with the given prefix, mapped to their counts.
     * If prefix is not present, returns an empty MutableMap.
     */
    fun getAutocompletionMap(prefix: String): MutableMap<String, Int> {
        var finalMap: MutableMap<String, Int> = mutableMapOf<String, Int>()
        var current: Node? = root
        current = findLastNode(prefix)
        finalMap = getMapHelper(current, prefix, finalMap)
        return finalMap
    }
    // iterates through all of the children of the last node to add to the map (divided so that this part handles the recursive)
    private fun getMapHelper(curr: Node?, string: String, map:MutableMap<String, Int>) : MutableMap<String, Int>  {
        if (curr?.children != null) {
            for ((char, child) in curr!!.children) {
                if (child.count > firstIndex) { 
                    map[string + char] = child.count //adds the current word to the map along with its count
                }
                getMapHelper(child, string + char, map)
            }
        }
        return map
    }
    // iterates through to find the child with access to all of the children of the last node in the prefix
    // contains but returns a node instead of a boolean
    private fun findLastNode(word: String): Node? {
        curr = root
        var lastNode:Node? = findLastHelper(word, curr)
        return lastNode
    }
    // handles recursive part of findLastNode
    private fun findLastHelper(word: String, node: Node?): Node? {
        var locNode: Node? = node
        var length = word.length
        var firstChar: Char = word[firstIndex]
        if (length == lengthCheck) {
            if (locNode?.children[firstChar] == null) { //if it doesnt exist, then it's 0
                return null
            } else {
                return locNode!!.children[firstChar]
            }
        } else {
            if (locNode?.children[firstChar] != null) {
                locNode = locNode?.children[firstChar]
                return findLastHelper(word.substring(secondChar,length), locNode)
            }
        }
        return null
    }
}

/*
* Reflection: This assignment was hard. I struggled with the 
idea of defining an instance variable (currNode) in recursive functions
but it was easier once I added helper functions. I spent about 7-8 hours total
on this assignment.
*/


/*  
* AI Log: I used AI to help me iterate through map entries in kotlin.
*/