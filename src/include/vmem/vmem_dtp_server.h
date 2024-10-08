#ifndef DIPP_DTP_SERVER_H
#define DIPP_DTP_SERVER_H

#include <string.h>
#include <stdio.h>
#include <dtp/dtp.h>
#include <dtp/platform.h>
#include <vmem/vmem.h>

static uint32_t payload_read(uint8_t payload_id, uint32_t offset, void *output, uint32_t size) {

    vmem_t *ring = NULL;
		for(vmem_t * vmem = (vmem_t *) &__start_vmem; vmem < (vmem_t *) &__stop_vmem; vmem++) {
			if (strncmp("images", vmem->name, 7) == 0) {
				ring = vmem;
				break;
			}
		}
	if (ring == NULL) return;

	vmem_ring_driver_t * driver = (vmem_ring_driver_t *)ring->driver;
    
    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;
    
	ring->read(ring, 0, output, payload_id);

    printf("Read to buffer: %s \n", output);

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

    printf("images ring buffer information \n");
    printf("data_size is %u \n", driver->data_size);
    printf("number of entries is %u \n", driver->entries);
    printf("tail is at %u \n", tail);
    printf("head is at %u \n", head);
    uint8_t index =  tail + payload_id;
    printf("actual index of requested payload is %u \n", index);
    if (index >= head) {
        printf("index out of ring buffer bounds \n");
        return false;
    }

    printf("offsets are: \n");
    for (int i = 0; i<=driver->entries; i++) {
        printf("    %u : %u \n", i, offsets[i]);
    }

    printf("offset for index is %u \n", offsets[index]);

    uint32_t payload_size;
    if (offsets[index+1] < offsets[index]) {
        payload_size = driver->data_size - offsets[index] + offsets[index+1];
    } else {
        payload_size = offsets[index+1] - offsets[index];
    }
    // NOTE: Extend VMEM ring_buffer interface with functionality to get size of payload at index

    printf("size of payload is %u \n", payload_size);

    meta->size = payload_size;
    meta->read = payload_read;

    return result;
}

#endif