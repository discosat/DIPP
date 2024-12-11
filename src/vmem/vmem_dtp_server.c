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


// Read the observation at specified index within ring buffer
static uint32_t observation_read(uint16_t index, uint32_t offset_within_observation, void *output, uint32_t size) {
    
    uint32_t offset_within_ring_buffer = vmem_ring_offset(&vmem_images, index, offset_within_observation);

	(&vmem_images)->read(&vmem_images, offset_within_ring_buffer, output, size);

    return size; // Assume that everything has been read, since the vmem api doesn't return any value to indicate how much data is read
}

// This method is implemented to tell the DTP server which payload to use
// The provided payload_id is intepreted as the index
bool get_payload_meta(dftp_payload_meta_t *meta, uint16_t payload_id) {

    int is_valid = vmem_ring_is_valid_index(&vmem_images, (uint32_t) payload_id);
    if (!is_valid) {
        return false;
    }
    uint32_t data_len = vmem_ring_element_size(&vmem_images, payload_id);

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

    uint32_t observation_amount = vmem_ring_get_amount_of_elements(&vmem_images);

    DTPMetadata *dtpmeta = malloc(sizeof(DTPMetadata));
    dtpmetadata__init(dtpmeta);
    dtpmeta->n_items = observation_amount;
    dtpmeta->items = malloc(observation_amount * sizeof(DTPMetadataItem*));

    for (uint32_t index = 0; index < observation_amount; index++) {

        uint32_t size = vmem_ring_element_size(&vmem_images, index);
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