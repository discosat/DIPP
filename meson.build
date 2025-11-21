project(
	'pipeline',
	'c',
	subproject_dir: 'lib',
	default_options: [
		'c_std=gnu11',
		'b_lto=false',
		'default_library=static',
		'csp:packet_padding_bytes=42',
		'csp:buffer_count=15000',
		'csp:buffer_size=2048',
		'csp:conn_max=20',
		'csp:conn_rxqueue_len=1000',
		'csp:qfifo_len=1000',
		'csp:rdp_max_window=1000',
		'csp:port_max_bind=16',
		'csp:use_rtable=true',
		'param:have_fopen=true',
		'param:list_dynamic=true',
		'param:vmem_fram=false',
	],
)

sources = files(
	'src/dipp_config.c',
	'src/dipp_error.c',
	'src/dipp_process.c',
	'src/main.c',
	'src/serial.c',
	'src/vmem/vmem_upload_local.c',
	'src/vmem/vmem_dtp_server.c',
	'src/vmem/vmem_storage.c',
	'src/vmem/vmem_ring_buffer.c',
	'src/vmem/vmem_upload_local.c',
	'src/protobuf/module_config.pb-c.c',
	'src/protobuf/pipeline_config.pb-c.c',
	'src/protobuf/metadata.pb-c.c',
	'src/protobuf/dtpmetadata.pb-c.c',
	'src/utils/minitrace.c',
	'src/utils/murmur_hash.c',
	'src/heuristics/best_effort_heuristic.c',
	'src/heuristics/default_effort.c',
	'src/heuristics/implementation_judge.c',
	'src/heuristics/lowest_effort_heuristic.c',
	'src/cost_store/cost_store.c',
	'src/cost_store/cost_store_mmap.c',
	'src/cost_store/cost_store_mem.c',
	'src/priority_queue/priority_queue.c',
	'src/priority_queue/priority_queue_mmap.c',
	'src/priority_queue/priority_queue_mem.c',
	'src/pipeline/pipeline_executor.c',
	'src/process/process_module.c',
	'src/image/image_store.c',
	'src/telemetry.c',
	'src/battery_simulator.c'
)

dirs = include_directories(
	'src/include',
	'src/include/protobuf',
	'src/include/param',
	'src/include/vmem',
	'src/include/process',
	'src/include/pipeline',
	'src/include/utils',
	'src/include/heuristics',
	'src/include/cost_store',
	'src/include/priority_queue',
	'src/include/image',
)

csp_dep = dependency('csp', fallback: ['csp', 'csp_dep'])
param_dep = dependency('param', fallback: ['param', 'param_dep'])
dtp_server_dep = dependency('dtp_server', fallback: ['dtp', 'dtp_server_dep'])
proto_c_dep = dependency('libprotobuf-c', fallback: ['protobuf-c', 'proto_c_dep'])
brotli_dep = dependency('libbrotlidec')
m_dep = meson.get_compiler('c').find_library('m', required : false)
uuid_dep = meson.get_compiler('c').find_library('uuid', required: false)
deps = [csp_dep,param_dep,dtp_server_dep,proto_c_dep,m_dep,brotli_dep, uuid_dep]

c_args = [
	'-DHOSTNAME="@0@"'.format(get_option('hostname')),
	'-DMTR_ENABLED=1'
	]

dipp = executable(
	'dipp',
	sources,
	include_directories: dirs,
	dependencies: deps,
	install: true,
	#c_args: ['-g2', '-O0', '-Wall', '-Wextra'] + c_args,
	c_args: c_args,
	link_args: ['-ldl'],
)
