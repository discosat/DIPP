#include "cost_store.h"

CostStoreImpl *get_cost_store_impl(StorageMode storage_type)
{

    // Initialize the cost cache based on the storage type
    switch (storage_type)
    {
    case STORAGE_MMAP:
        return &cost_store_mmap;
    case STORAGE_MEM:
        return &cost_store_mem;
    default:
        return NULL;
    }
}
