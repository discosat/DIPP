project('ring_buffer_upload', 'c', subproject_dir: 'lib')

param_dep = dependency('param', fallback: ['param', 'param_dep'])

executable('ring_buffer_upload', 'ring_buffer_upload.c', dependencies : param_dep)
