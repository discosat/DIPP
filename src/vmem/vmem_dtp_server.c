#include <dtp/dtp.h>
#include <dtp/platform.h>
#include <vmem/vmem.h>
#include <vmem/vmem_ring.h>

static uint32_t element_size(vmem_t * vmem, uint32_t offset) {
    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)vmem->driver;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    int offset_int = (int)offset;
    uint32_t read_from_index = offset_int < 0 // supports negative indexing from head
        ? (head + offset_int + driver->entries) % driver->entries
        : (tail + offset_int) % driver->entries;
    uint32_t read_to_index = (read_from_index + 1) % driver->entries;

    uint32_t read_from_offset = offsets[read_from_index];
    uint32_t read_to_offset = offsets[read_to_index];

    int wraparound = read_to_offset < read_from_offset; 

    uint32_t len;

    if (wraparound) {
        len = driver->data_size - read_from_offset + read_to_offset;
    } else {
        len = read_to_offset - read_from_offset;
    }

    return len;
}

static int is_valid_index(vmem_t * vmem, uint32_t offset) {
    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)vmem->driver;

    if (offset >= driver->entries) return 0;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    int offset_int = (int)offset;

    uint32_t index = offset_int < 0
        ? (head + offset_int + driver->entries) % driver->entries
        : (tail + offset_int) % driver->entries;

    int wraparound = driver->head < driver->tail;

    if (wraparound) {
        return !((head <= index) && (index < tail));
    } else {
        return (tail <= index) && (index < head);
    }
}

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
    int is_valid = is_valid_index(ring, payload_id);
    if (!is_valid) {
        printf("index out of ring buffer bounds \n");
        return false;
    }
    uint32_t data_len = element_size(ring, payload_id);
    printf("length of payload element is %u \n", data_len);

    printf("offsets are: \n");
    for (int i = 0; i<=driver->entries; i++) {
        printf("    %u : %u \n", i, offsets[i]);
    }

    meta->size = data_len;
    meta->read = payload_read;

    return result;
}