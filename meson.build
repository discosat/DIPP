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
	'src/quarantine.c',
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
	'src/heuristics/sched_policy.c',
	'src/heuristics/lowest_effort_heuristic.c',
	'src/cost_store/cost_store.c',
	'src/cost_store/cost_store_mmap.c',
	'src/cost_store/cost_store_mem.c',
	'src/cost_store/cost_model.c',
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

# Build identifier: git describe + submodule SHAs, compiled in so every run
# and every trace can be tied back to an exact source state.
build_id = 'unknown'
build_id_prog = find_program('tools/build_id.sh', required: false)
if build_id_prog.found()
	build_id_res = run_command(build_id_prog, check: false)
	if build_id_res.returncode() == 0 and build_id_res.stdout().strip() != ''
		build_id = build_id_res.stdout().strip()
	endif
endif
message('BUILD_ID: ' + build_id)

c_args = [
	'-DHOSTNAME="@0@"'.format(get_option('hostname')),
	'-DMTR_ENABLED=1',
	'-DBUILD_ID="@0@"'.format(build_id),
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

# Harness control tool: sends mtype-2 messages on the daemon's own SysV queue
# (see src/include/dipp_control.h). Board-side, staged and deployed alongside
# the daemon; no CSP or libparam, so it links against nothing.
dipp_ctl = executable(
	'dipp_ctl',
	files('tools/dipp_ctl.c'),
	include_directories: dirs,
	# dipp_process.h (for MSG_QUEUE_KEY) pulls in the generated protobuf
	# headers, so the dependency is for the include path, not for code.
	dependencies: [proto_c_dep],
	install: true,
	c_args: c_args,
)
