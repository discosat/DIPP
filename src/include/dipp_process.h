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
extern CostStore *cost_store;
extern StorageMode global_storage_mode;

// Main processing loop running in a thread
// This loop first initialiazes queues, which possibly already hold elements,
// if using MMAP. It then continuously drains the message queue and
// pushes new data onto the ingest priority queue.
// It then pulls data from the partially processed queue first, processes a single batch,
// and optionally also pulls from the ingest queue if the partially processed queue is not full.
// Processed data is either pushed back onto the partially processed queue (if not fully processed)
// or uploaded (if fully processed).
void process_images_loop();

#endif