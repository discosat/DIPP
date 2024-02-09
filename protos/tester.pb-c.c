/* Generated by the protocol buffer compiler.  DO NOT EDIT! */
/* Generated from: protos/tester.proto */

/* Do not generate deprecated warnings for self */
#ifndef PROTOBUF_C__NO_DEPRECATED
#define PROTOBUF_C__NO_DEPRECATED
#endif

#include "protos/tester.pb-c.h"
void   person__init
                     (Person         *message)
{
  static const Person init_value = PERSON__INIT;
  *message = init_value;
}
size_t person__get_packed_size
                     (const Person *message)
{
  assert(message->base.descriptor == &person__descriptor);
  return protobuf_c_message_get_packed_size ((const ProtobufCMessage*)(message));
}
size_t person__pack
                     (const Person *message,
                      uint8_t       *out)
{
  assert(message->base.descriptor == &person__descriptor);
  return protobuf_c_message_pack ((const ProtobufCMessage*)message, out);
}
size_t person__pack_to_buffer
                     (const Person *message,
                      ProtobufCBuffer *buffer)
{
  assert(message->base.descriptor == &person__descriptor);
  return protobuf_c_message_pack_to_buffer ((const ProtobufCMessage*)message, buffer);
}
Person *
       person__unpack
                     (ProtobufCAllocator  *allocator,
                      size_t               len,
                      const uint8_t       *data)
{
  return (Person *)
     protobuf_c_message_unpack (&person__descriptor,
                                allocator, len, data);
}
void   person__free_unpacked
                     (Person *message,
                      ProtobufCAllocator *allocator)
{
  if(!message)
    return;
  assert(message->base.descriptor == &person__descriptor);
  protobuf_c_message_free_unpacked ((ProtobufCMessage*)message, allocator);
}
static const ProtobufCFieldDescriptor person__field_descriptors[3] =
{
  {
    "name",
    1,
    PROTOBUF_C_LABEL_NONE,
    PROTOBUF_C_TYPE_STRING,
    0,   /* quantifier_offset */
    offsetof(Person, name),
    NULL,
    &protobuf_c_empty_string,
    0,             /* flags */
    0,NULL,NULL    /* reserved1,reserved2, etc */
  },
  {
    "id",
    2,
    PROTOBUF_C_LABEL_NONE,
    PROTOBUF_C_TYPE_INT32,
    0,   /* quantifier_offset */
    offsetof(Person, id),
    NULL,
    NULL,
    0,             /* flags */
    0,NULL,NULL    /* reserved1,reserved2, etc */
  },
  {
    "email",
    3,
    PROTOBUF_C_LABEL_NONE,
    PROTOBUF_C_TYPE_STRING,
    0,   /* quantifier_offset */
    offsetof(Person, email),
    NULL,
    &protobuf_c_empty_string,
    0,             /* flags */
    0,NULL,NULL    /* reserved1,reserved2, etc */
  },
};
static const unsigned person__field_indices_by_name[] = {
  2,   /* field[2] = email */
  1,   /* field[1] = id */
  0,   /* field[0] = name */
};
static const ProtobufCIntRange person__number_ranges[1 + 1] =
{
  { 1, 0 },
  { 0, 3 }
};
const ProtobufCMessageDescriptor person__descriptor =
{
  PROTOBUF_C__MESSAGE_DESCRIPTOR_MAGIC,
  "Person",
  "Person",
  "Person",
  "",
  sizeof(Person),
  3,
  person__field_descriptors,
  person__field_indices_by_name,
  1,  person__number_ranges,
  (ProtobufCMessageInit) person__init,
  NULL,NULL,NULL    /* reserved[123] */
};
