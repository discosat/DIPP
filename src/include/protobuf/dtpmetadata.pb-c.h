/* Generated by the protocol buffer compiler.  DO NOT EDIT! */
/* Generated from: protos/dtpmetadata.proto */

#ifndef PROTOBUF_C_protos_2fdtpmetadata_2eproto__INCLUDED
#define PROTOBUF_C_protos_2fdtpmetadata_2eproto__INCLUDED

#include <protobuf-c/protobuf-c.h>

PROTOBUF_C__BEGIN_DECLS

#if PROTOBUF_C_VERSION_NUMBER < 1003000
# error This file was generated by a newer version of protoc-c which is incompatible with your libprotobuf-c headers. Please update your headers.
#elif 1005000 < PROTOBUF_C_MIN_COMPILER_VERSION
# error This file was generated by an older version of protoc-c which is incompatible with your libprotobuf-c headers. Please regenerate this file with a newer version of protoc-c.
#endif


typedef struct DTPMetadataItem DTPMetadataItem;
typedef struct DTPMetadata DTPMetadata;


/* --- enums --- */


/* --- messages --- */

struct  DTPMetadataItem
{
  ProtobufCMessage base;
  int32_t payload_id;
  int32_t size;
  int32_t obid;
};
#define DTPMETADATA_ITEM__INIT \
 { PROTOBUF_C_MESSAGE_INIT (&dtpmetadata_item__descriptor) \
    , 0, 0, 0 }


struct  DTPMetadata
{
  ProtobufCMessage base;
  size_t n_items;
  DTPMetadataItem **items;
};
#define DTPMETADATA__INIT \
 { PROTOBUF_C_MESSAGE_INIT (&dtpmetadata__descriptor) \
    , 0,NULL }


/* DTPMetadataItem methods */
void   dtpmetadata_item__init
                     (DTPMetadataItem         *message);
size_t dtpmetadata_item__get_packed_size
                     (const DTPMetadataItem   *message);
size_t dtpmetadata_item__pack
                     (const DTPMetadataItem   *message,
                      uint8_t             *out);
size_t dtpmetadata_item__pack_to_buffer
                     (const DTPMetadataItem   *message,
                      ProtobufCBuffer     *buffer);
DTPMetadataItem *
       dtpmetadata_item__unpack
                     (ProtobufCAllocator  *allocator,
                      size_t               len,
                      const uint8_t       *data);
void   dtpmetadata_item__free_unpacked
                     (DTPMetadataItem *message,
                      ProtobufCAllocator *allocator);
/* DTPMetadata methods */
void   dtpmetadata__init
                     (DTPMetadata         *message);
size_t dtpmetadata__get_packed_size
                     (const DTPMetadata   *message);
size_t dtpmetadata__pack
                     (const DTPMetadata   *message,
                      uint8_t             *out);
size_t dtpmetadata__pack_to_buffer
                     (const DTPMetadata   *message,
                      ProtobufCBuffer     *buffer);
DTPMetadata *
       dtpmetadata__unpack
                     (ProtobufCAllocator  *allocator,
                      size_t               len,
                      const uint8_t       *data);
void   dtpmetadata__free_unpacked
                     (DTPMetadata *message,
                      ProtobufCAllocator *allocator);
/* --- per-message closures --- */

typedef void (*DTPMetadataItem_Closure)
                 (const DTPMetadataItem *message,
                  void *closure_data);
typedef void (*DTPMetadata_Closure)
                 (const DTPMetadata *message,
                  void *closure_data);

/* --- services --- */


/* --- descriptors --- */

extern const ProtobufCMessageDescriptor dtpmetadata_item__descriptor;
extern const ProtobufCMessageDescriptor dtpmetadata__descriptor;

PROTOBUF_C__END_DECLS


#endif  /* PROTOBUF_C_protos_2fdtpmetadata_2eproto__INCLUDED */
