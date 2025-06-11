#ifndef DIPP_PROCESS_H
#define DIPP_PROCESS_H

#include "dipp_config.h"
#include "priority_queue.h"
#include "cost_store.h"
#include "image_batch.h"

#define MSG_QUEUE_KEY 71

// Return codes
#define SUCCESS 0
#define FAILURE -1

extern PriorityQueue *ingest_pq;
extern PriorityQueue *partially_processed_pq;
extern CostEntry *cost_cache;
extern StorageMode global_storage_mode;

void process_images_loop();

#endif