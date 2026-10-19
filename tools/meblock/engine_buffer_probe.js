export function meblockEngineBufferFootprint() {
    const buffers = {
        filter_data: filterData.buffer.byteLength,
        origin_trie: origHNTrieContainer.buf.buffer.byteLength,
        destination_trie: destHNTrieContainer.buf.buffer.byteLength,
        pattern_trie: bidiTrie.buf8.buffer.byteLength,
    };
    return {
        schema: 'meblock-engine-buffers-v1',
        allocated_bytes: buffers,
        total_known_allocated_bytes: Object.values(buffers).reduce((sum, size) => sum + size, 0),
        filter_data_used_bytes: filterDataWritePtr * Int32Array.BYTES_PER_ELEMENT,
        filter_references_count: filterRefsWritePtr,
        compiled_network_filters: staticNetFilteringEngine.getFilterCount(),
        wasm_enabled: {
            origin_trie: origHNTrieContainer.wasmMemory !== null,
            destination_trie: destHNTrieContainer.wasmMemory !== null,
            pattern_trie: bidiTrie.wasmMemory !== null,
        },
    };
}
