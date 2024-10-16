#include <stdio.h>
#include <vmem/vmem_ring.h>
#include <vmem/vmem.h>

int main(int argc, char *argv[]) {

    if (argc < 3)
    {
        printf("Missing arguments: Expected <ring_buffer_name> <file_name>");
        return -1;
    }

    char * ring_buffer_name = argv[1];
    char * file_name = argv[2];

    vmem_t *ring = NULL;
    for(vmem_t * vmem = (vmem_t *) &__start_vmem; vmem < (vmem_t *) &__stop_vmem; vmem++) {
        if (strncmp(ring_buffer_name, vmem->name, 20)) {
            ring = vmem;
            break;
        }
    }
    if (ring == NULL) {
        printf("Could not find %s.vmem", ring_buffer_name);
        return -1;
    }

    FILE *file = fopen(file_name, "rb");
    if (file == NULL) {
        printf("Could not open %s", file_name);
        return -1;
    }

    fseek(file, 0, SEEK_END);
    long fsize = ftell(file);
    fseek(file, 0, SEEK_SET); 

    char *buffer = malloc(fsize + 1);
    fread(buffer, fsize, 1, file);
    fclose(file);

    buffer[fsize] = 0;
    // Thank you https://stackoverflow.com/questions/14002954/c-how-to-read-an-entire-file-into-a-buffer

    ring->write(ring, 0, buffer, fsize);
    free(buffer);

    return 0;
}