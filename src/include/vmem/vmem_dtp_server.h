#ifndef DIPP_DTP_SERVER_H
#define DIPP_DTP_SERVER_H

#include <dtp/dtp.h>
#include <dtp/platform.h>
#include <vmem/vmem.h>

static uint32_t payload_read(uint8_t payload_id, uint32_t offset, void *output, uint32_t size) {

	const char helloworld[] = "Hello, World!";

    printf("Reading payload \n");
    printf("id is %u \n", payload_id);
    printf("offset is %u \n", offset);
    printf("size is %u \n", size);

    printf("attempting to find images ring_buffer \n");
    vmem_t *ring = NULL;
		for(vmem_t * vmem = (vmem_t *) &__start_vmem; vmem < (vmem_t *) &__stop_vmem; vmem++) {
			if (strncmp("images", vmem->name, 7) == 0) {
				ring = vmem;
				break;
			}
		}
	if (ring == NULL) return;

    printf("found images ring_buffer \n");

	vmem_ring_driver_t * driver = (vmem_ring_driver_t *)ring->driver;
    
    printf("images ring buffer information \n");
    printf("data_size is %u \n", driver->data_size);
    printf("number of entries is %u \n", driver->entries);
    printf("tail is at %u \n", driver->tail);
    printf("head is at %u \n", driver->head);

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;
    
    printf("offsets are: \n");
    for (int i = tail; i<=head; i++) {
        printf("    %u : %u \n", i, offsets[i]);
    }

	ring->read(ring, 0, output, offsets[payload_id]);

    return size;
}

/* This method is implemented to tell the DTP server which payload to use */
bool get_payload_meta(dftp_payload_meta_t *meta, uint8_t payload_id) {
    bool result = true;

	printf("call to get_payload_meta");

    printf("attempting to find images ring_buffer \n");
    vmem_t *ring = NULL;
		for(vmem_t * vmem = (vmem_t *) &__start_vmem; vmem < (vmem_t *) &__stop_vmem; vmem++) {
			if (strncmp("images", vmem->name, 7) == 0) {
				ring = vmem;
				break;
			}
		}
	if (ring == NULL) return;

    printf("found images ring_buffer \n");

    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)ring->driver;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;
    
    meta->size = offsets[payload_id+1]-offsets[payload_id];
    meta->read = payload_read;

    return result;
}

#endif