#include <dtp/dtp.h>
#include <dtp/platform.h>
#include <vmem/vmem.h>
#include <vmem/vmem_ring.h>
#include "vmem_ring_buffer.h"
#include "protobuf/metadata.pb-c.h"

// For these static methods, consider moving them into libparam, having vmem reference as parameter
// Look at the naming, (offset, index, payload_id) (payload, element), be more consistent...

// Calculate the offset of a ring buffer element within the ring buffer based on the payload id
// Doesn't take metadata at start of ring buffer into account
static uint32_t ring_buffer_offset(uint32_t payload_id, uint32_t offset) {
    
    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    uint32_t ring_buffer_index = payload_id < 0 // supports negative indexing from head
        ? (head + payload_id + driver->entries) % driver->entries
        : (tail + payload_id) % driver->entries;

    uint32_t offset_of_payload = offsets[ring_buffer_index];

    return offset_of_payload + offset;

}

// Find the size of a ring buffer element based on the payload_id
static uint32_t element_size(uint32_t offset) {
    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

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

// Check if the payload_id provided results in a valid ring buffer index
static int is_valid_index(uint32_t payload_id) {
    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

    if (payload_id >= driver->entries) return 0;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    int offset_int = (int)payload_id;

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
    uint32_t res = 0;

	vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;
    
    uint32_t ring_offset = ring_buffer_offset(payload_id, offset);

	(&vmem_images)->read(&vmem_images, ring_offset, output, size);

    return res;
}

/* This method is implemented to tell the DTP server which payload to use */
bool get_payload_meta(dftp_payload_meta_t *meta, uint8_t payload_id) {
	printf("call to get_payload_meta");

    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    printf("images ring buffer information \n");
    printf("data_size is %u \n", driver->data_size);
    printf("number of entries is %u \n", driver->entries);
    printf("tail is at %u \n", tail);
    printf("head is at %u \n", head);
    int is_valid = is_valid_index(payload_id);
    if (!is_valid) {
        printf("index out of ring buffer bounds \n");
        return false;
    }
    uint32_t data_len = element_size(payload_id);
    printf("length of payload element is %u \n", data_len);

    printf("offsets are: \n");
    for (int i = 0; i<=driver->entries; i++) {
        printf("    %u : %u \n", i, offsets[i]);
    }

    meta->size = data_len;
    meta->read = payload_read;

    return true;
}

// RingBufferElementMetadata *ring_buffer_element_info(uint32_t offset) {

//     uint32_t element_len = element_size(offset);
//     char *output = malloc(element_size); // this should perhaps be allocated statically instead

//     printf("Reading element of size %u \n", element_len);

//     ring->read(vmem_images, 0, output, payload_id);

//     printf("Read element \n");

//     uint32_t meta_size = *((uint32_t *)output);

//     printf("metadata size is %u \n", meta_size);

//     // In general, there is some memory stuff im unsure about, someone needs to look at this code...

//     Metadata *metadata = metadata__unpack(NULL, meta_size, output + sizeof(uint32_t));
//     struct RingBufferElementMetadata *element_metadata = malloc(RingBufferElementMetadata);
//     element_metadata->offset;
//     element_metadata->metadata_len = meta_size;
//     element_metadata->metadata = metadata;
//     return element_metadata;
// }

// RingBufferMetadata *ring_buffer_info() {

//     vmem_ring_driver_t * driver = (vmem_ring_driver_t *)vmem_images->driver;

//     struct RingBufferMetadata *ring_buffer_metadata = malloc(RingBufferMetadata);

//     // WIP

//     int index = driver->tail;
//     while (index != head) {
//         index = (index + 1) % driver->entries;
//     }


    
// }