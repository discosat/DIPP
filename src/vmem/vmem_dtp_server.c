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

typedef struct observation_meta {
    uint16_t index;
    uint32_t size;
    uint32_t obid;
} observation_meta_t;

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
    uint8_t meta_buf[meta_size];
    observation_read(index, 4, meta_buf, meta_size);
    Metadata *metadata = metadata__unpack(NULL, meta_size, meta_buf);
    return metadata;
}

// Get metadata for a specific observation which is to be transferred, in this case only OBID, size and index
observation_meta_t observation_get_dtp_meta(uint16_t index) {
    Metadata *meta = observation_get_metadata(index);

    observation_meta_t obs_meta = {
        .index = index,
        .size = meta->size,
        .obid = meta->obid,
    };

    metadata__free_unpacked(meta, NULL);

    return obs_meta;
}

// Server for serving metadata of ring buffer observation
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

        csp_packet_t *request = csp_read(conn, 50);

        if (request->data[0] == DIPP_DTP_OBSERVATION_AMOUNT_REQUEST) { 
            uint32_t observation_amount = vmem_ring_get_amount_of_elements(&vmem_images);

            csp_packet_t *response = csp_buffer_get(sizeof(uint32_t));
            response->length = sizeof(uint32_t);
            memcpy(response->data, &observation_amount, sizeof(uint32_t));
            csp_send(conn, response);
            csp_buffer_free(response);

        } else if (request->data[0] == DIPP_DTP_OBSERVATION_META_REQUEST) {
            uint16_t index = request->data[1] + (request->data[2] << 8);

            int is_valid = vmem_ring_is_valid_index(&vmem_images, (uint32_t) index); // change to uint16_t...
            if (!is_valid) {
                csp_packet_t *invalid_index_response = csp_buffer_get(1);
                invalid_index_response->length = 1;
                invalid_index_response->data[0] = 0;
                csp_send(conn, invalid_index_response);
                csp_buffer_free(invalid_index_response);
            } else {
                observation_meta_t obs_meta = observation_get_dtp_meta(index);

                csp_packet_t *response = csp_buffer_get(sizeof(observation_meta_t) + 1);
                response->length = sizeof(observation_meta_t) + 1;
                response->data[0] = 1;
                memcpy(response->data+1, &obs_meta, sizeof(observation_meta_t));
                csp_send(conn, response);
                
                csp_buffer_free(response);
            }
        }

        csp_buffer_free(request);

        csp_close(conn);
    }

}