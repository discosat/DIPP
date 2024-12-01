/* Generated by the protocol buffer compiler.  DO NOT EDIT! */
/* Generated from: protos/dtpmetadata.proto */

/* Do not generate deprecated warnings for self */
#ifndef PROTOBUF_C__NO_DEPRECATED
#define PROTOBUF_C__NO_DEPRECATED
#endif

#include "dtpmetadata.pb-c.h"
void   dtpmetadata_item__init
                     (DTPMetadataItem         *message)
{
  static const DTPMetadataItem init_value = DTPMETADATA_ITEM__INIT;
  *message = init_value;
}
size_t dtpmetadata_item__get_packed_size
                     (const DTPMetadataItem *message)
{
  assert(message->base.descriptor == &dtpmetadata_item__descriptor);
  return protobuf_c_message_get_packed_size ((const ProtobufCMessage*)(message));
}
size_t dtpmetadata_item__pack
                     (const DTPMetadataItem *message,
                      uint8_t       *out)
{
  assert(message->base.descriptor == &dtpmetadata_item__descriptor);
  return protobuf_c_message_pack ((const ProtobufCMessage*)message, out);
}
size_t dtpmetadata_item__pack_to_buffer
                     (const DTPMetadataItem *message,
                      ProtobufCBuffer *buffer)
{
  assert(message->base.descriptor == &dtpmetadata_item__descriptor);
  return protobuf_c_message_pack_to_buffer ((const ProtobufCMessage*)message, buffer);
}
DTPMetadataItem *
       dtpmetadata_item__unpack
                     (ProtobufCAllocator  *allocator,
                      size_t               len,
                      const uint8_t       *data)
{
  return (DTPMetadataItem *)
     protobuf_c_message_unpack (&dtpmetadata_item__descriptor,
                                allocator, len, data);
}
void   dtpmetadata_item__free_unpacked
                     (DTPMetadataItem *message,
                      ProtobufCAllocator *allocator)
{
  if(!message)
    return;
  assert(message->base.descriptor == &dtpmetadata_item__descriptor);
  protobuf_c_message_free_unpacked ((ProtobufCMessage*)message, allocator);
}
void   dtpmetadata__init
                     (DTPMetadata         *message)
{
  static const DTPMetadata init_value = DTPMETADATA__INIT;
  *message = init_value;
}
size_t dtpmetadata__get_packed_size
                     (const DTPMetadata *message)
{
  assert(message->base.descriptor == &dtpmetadata__descriptor);
  return protobuf_c_message_get_packed_size ((const ProtobufCMessage*)(message));
}
size_t dtpmetadata__pack
                     (const DTPMetadata *message,
                      uint8_t       *out)
{
  assert(message->base.descriptor == &dtpmetadata__descriptor);
  return protobuf_c_message_pack ((const ProtobufCMessage*)message, out);
}
size_t dtpmetadata__pack_to_buffer
                     (const DTPMetadata *message,
                      ProtobufCBuffer *buffer)
{
  assert(message->base.descriptor == &dtpmetadata__descriptor);
  return protobuf_c_message_pack_to_buffer ((const ProtobufCMessage*)message, buffer);
}
DTPMetadata *
       dtpmetadata__unpack
                     (ProtobufCAllocator  *allocator,
                      size_t               len,
                      const uint8_t       *data)
{
  return (DTPMetadata *)
     protobuf_c_message_unpack (&dtpmetadata__descriptor,
                                allocator, len, data);
}
void   dtpmetadata__free_unpacked
                     (DTPMetadata *message,
                      ProtobufCAllocator *allocator)
{
  if(!message)
    return;
  assert(message->base.descriptor == &dtpmetadata__descriptor);
  protobuf_c_message_free_unpacked ((ProtobufCMessage*)message, allocator);
}
static const ProtobufCFieldDescriptor dtpmetadata_item__field_descriptors[3] =
{
  {
    "payload_id",
    1,
    PROTOBUF_C_LABEL_NONE,
    PROTOBUF_C_TYPE_INT32,
    0,   /* quantifier_offset */
    offsetof(DTPMetadataItem, payload_id),
    NULL,
    NULL,
    0,             /* flags */
    0,NULL,NULL    /* reserved1,reserved2, etc */
  },
  {
    "size",
    2,
    PROTOBUF_C_LABEL_NONE,
    PROTOBUF_C_TYPE_INT32,
    0,   /* quantifier_offset */
    offsetof(DTPMetadataItem, size),
    NULL,
    NULL,
    0,             /* flags */
    0,NULL,NULL    /* reserved1,reserved2, etc */
  },
  {
    "obid",
    3,
    PROTOBUF_C_LABEL_NONE,
    PROTOBUF_C_TYPE_INT32,
    0,   /* quantifier_offset */
    offsetof(DTPMetadataItem, obid),
    NULL,
    NULL,
    0,             /* flags */
    0,NULL,NULL    /* reserved1,reserved2, etc */
  },
};
static const unsigned dtpmetadata_item__field_indices_by_name[] = {
  2,   /* field[2] = obid */
  0,   /* field[0] = payload_id */
  1,   /* field[1] = size */
};
static const ProtobufCIntRange dtpmetadata_item__number_ranges[1 + 1] =
{
  { 1, 0 },
  { 0, 3 }
};
const ProtobufCMessageDescriptor dtpmetadata_item__descriptor =
{
  PROTOBUF_C__MESSAGE_DESCRIPTOR_MAGIC,
  "DTPMetadataItem",
  "DTPMetadataItem",
  "DTPMetadataItem",
  "",
  sizeof(DTPMetadataItem),
  3,
  dtpmetadata_item__field_descriptors,
  dtpmetadata_item__field_indices_by_name,
  1,  dtpmetadata_item__number_ranges,
  (ProtobufCMessageInit) dtpmetadata_item__init,
  NULL,NULL,NULL    /* reserved[123] */
};
static const ProtobufCFieldDescriptor dtpmetadata__field_descriptors[1] =
{
  {
    "items",
    1,
    PROTOBUF_C_LABEL_REPEATED,
    PROTOBUF_C_TYPE_MESSAGE,
    offsetof(DTPMetadata, n_items),
    offsetof(DTPMetadata, items),
    &dtpmetadata_item__descriptor,
    NULL,
    0,             /* flags */
    0,NULL,NULL    /* reserved1,reserved2, etc */
  },
};
static const unsigned dtpmetadata__field_indices_by_name[] = {
  0,   /* field[0] = items */
};
static const ProtobufCIntRange dtpmetadata__number_ranges[1 + 1] =
{
  { 1, 0 },
  { 0, 1 }
};
const ProtobufCMessageDescriptor dtpmetadata__descriptor =
{
  PROTOBUF_C__MESSAGE_DESCRIPTOR_MAGIC,
  "DTPMetadata",
  "DTPMetadata",
  "DTPMetadata",
  "",
  sizeof(DTPMetadata),
  1,
  dtpmetadata__field_descriptors,
  dtpmetadata__field_indices_by_name,
  1,  dtpmetadata__number_ranges,
  (ProtobufCMessageInit) dtpmetadata__init,
  NULL,NULL,NULL    /* reserved[123] */
};
