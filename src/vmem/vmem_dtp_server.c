#include "vmem_dtp_server.h"
#include <dtp/dtp.h>
#include <dtp/platform.h>
#include <vmem/vmem.h>
#include <vmem/vmem_ring.h>
#include "vmem_ring_buffer.h"
#include <stdio.h>
#include <stdlib.h>
#include <csp/csp.h>
#include "metadata.pb-c.h"
#include "dtpmetadata.pb-c.h"

// For these static methods, consider moving them into libparam, having vmem reference as parameter

// Calculate the offset of a observation within the ring buffer based on the index
static uint32_t ring_buffer_offset(uint32_t index, uint32_t offset) {
    
    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    uint32_t ring_buffer_index = (tail + index) % driver->entries;

    uint32_t offset_of_observation = offsets[ring_buffer_index];

    return offset_of_observation + offset;

}

// Find the size of a observation within the ring buffer based on the index
static uint32_t observation_size(uint32_t offset) {
    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    uint32_t read_from_index = (tail + offset) % driver->entries;
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

// Check if the index provided is a valid ring buffer index
static int is_valid_index(uint32_t index) {
    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

    if (index >= driver->entries) return 0;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    uint32_t ring_buffer_index = (tail + index) % driver->entries;

    int wraparound = driver->head < driver->tail;

    if (wraparound) {
        return !((head <= ring_buffer_index) && (ring_buffer_index < tail));
    } else {
        return (tail <= ring_buffer_index) && (ring_buffer_index < head);
    }
}

static uint32_t observation_read(uint16_t index, uint32_t offset_within_observation, void *output, uint32_t size) {
	vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;
    
    uint32_t offset_within_ring_buffer = ring_buffer_offset(index, offset_within_observation);

	(&vmem_images)->read(&vmem_images, offset_within_ring_buffer, output, size);

    return size; // Assume that everything has been read, since the vmem api doesn't return any value to indicate how much data is read
}

/* This method is implemented to tell the DTP server which payload to use */
bool get_payload_meta(dftp_payload_meta_t *meta, uint16_t payload_id) {

    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

    uint32_t head = driver->head;
    uint32_t tail = driver->tail;
    uint32_t * offsets = (uint32_t *)driver->offsets;

    int is_valid = is_valid_index(payload_id);
    if (!is_valid) {
        return false;
    }
    uint32_t data_len = observation_size(payload_id);

    meta->size = data_len;
    meta->read = observation_read;

    return true;
}

// Get size of metadata for a specific observation
static uint32_t observation_get_meta_size(uint16_t index) {
    uint32_t meta_size;
    uint8_t meta_size_buf[sizeof(meta_size)];
    observation_read(index, 0, meta_size_buf, sizeof(meta_size)); // The first 4 bytes of each observation is the size of the metadata section
    memcpy(&meta_size, (uint32_t *) meta_size_buf, sizeof(meta_size));
    return meta_size;
}

// Get metadata of a specific observation
static Metadata *observation_get_metadata(uint16_t index) {
    uint32_t meta_size = observation_get_meta_size(index);
    char meta_buf[meta_size];
    observation_read(index, 4, meta_buf, meta_size);
    Metadata *metadata = metadata__unpack(NULL, meta_size, meta_buf);
    return metadata;
}

// Get metadata information about indeces
static DTPMetadata *get_indeces_metadata() {

    vmem_ring_driver_t * driver = (vmem_ring_driver_t *)(&vmem_images)->driver;

    int observation_amount;
    if (driver->head >= driver->tail) {
        observation_amount = driver->head - driver->tail;
    } else {
        observation_amount = driver->entries - driver->tail + driver->head;
    }

    DTPMetadata *dtpmeta = malloc(sizeof(DTPMetadata));
    dtpmetadata__init(dtpmeta);
    dtpmeta->n_items = observation_amount;
    dtpmeta->items = malloc(observation_amount * sizeof(DTPMetadataItem*));

    for (int index = 0; index < observation_amount; index++) {
        int size = observation_size(index);
        Metadata *metadata = observation_get_metadata(index);
        DTPMetadataItem *item = malloc(sizeof(DTPMetadataItem));
        dtpmetadata_item__init(item);
        item->payload_id = index;
        item->size = size;
        item->obid = 0;
        dtpmeta->items[index] = item;
    }

    return dtpmeta;
}

// Server for serving indeces of ring buffer
void dtp_indeces_server() {    
    
    static csp_socket_t sock = {0};
    sock.opts = CSP_O_RDP;
    csp_bind(&sock, INDECES_PORT);
    csp_listen(&sock, 1); // This allows only one simultaneous connection

    csp_conn_t *conn;

    while (1) {
        if ((conn = csp_accept(&sock, 10000)) == NULL)
        {
            continue;
        }

        DTPMetadata *dtpmetadata = get_indeces_metadata();

        uint32_t dtpmetadata_size = dtpmetadata__get_packed_size(dtpmetadata);

        csp_packet_t * length_packet = csp_buffer_get(sizeof(uint32_t));
        length_packet->length = sizeof(uint32_t);
        memcpy(length_packet->data, &dtpmetadata_size, sizeof(uint32_t));
        csp_send(conn, length_packet);

        // Pack dtpmetadata struct into a buffer
        uint8_t *dtpmetadata_packed = malloc(dtpmetadata_size);
        dtpmetadata__pack(dtpmetadata, dtpmetadata_packed);        

        // Send contents of buffer
        int count = 0;
        while((count < dtpmetadata_size) && csp_conn_is_active(conn)) {

            csp_packet_t * packet = csp_buffer_get(INDECES_PACKET_SIZE);
            packet->length = dtpmetadata_size - count < INDECES_PACKET_SIZE ? dtpmetadata_size - count : INDECES_PACKET_SIZE;
            memcpy(packet->data, (void *) (dtpmetadata_packed + count), packet->length);

            count += packet->length;
            csp_send(conn, packet);
        }

        csp_close(conn);
    }

}