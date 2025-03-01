import argparse
import glob
import json
import nibabel as nib
import nipype.interfaces.fsl as fsl
import numpy as np
import os
import re
import shutil
import subprocess
import sys
import tarfile
from zipfile import ZipFile 

# *****************************
# *** Constant Variables - CHECK THAT THESE ARE CORRECT
# *****************************

cardiac_bids = {
    "StartTime": -30.0,
    "SamplingFrequency": .01,
    "Columns": ["cardiac"]
}

respiratory_bids = {
    "StartTime": -30.0,
    "SamplingFrequency":  .04,
    "Columns": ["respiratory"]
}


# *****************************
# *** HELPER FUNCTIONS
# *****************************

#standardizes file names
def clean_file(f):
    f = f.replace('task_', 'task-').replace('run_','run-').replace('_ssg','')
    return f
#renames files with standardized file names
def cleanup(path):
    for f in glob.glob(os.path.join(path, '*')):
        new_name = clean_file(f)
        os.rename(f,new_name)
        
        
def bids_anat(sub_id, anat_dir, anat_path):
    """
    Moves and converts a anatomical folder associated with a T1 or T2
    to BIDS format. Assumes files are organized appropriately
    (e.g. "project_data/s999/ses-1/anat-[T1w,T2w]")
    """
    #T1 or T2?
    path_pieces = anat_dir.split('/')
    anat_type = [x for x in path_pieces if 'anat' in x][0].split('-')[1]
    anat_file = glob.glob(os.path.join(anat_dir,'*nii.gz'))
    assert len(anat_file) == 1, "More than one anat file found in directory %s" % anat_dir
    new_file = os.path.join(anat_path, sub_id + '_' + anat_type + '.nii.gz')
    if not os.path.exists(new_file):
        to_write.write('\tDefacing...\n')
        subprocess.call("pydeface %s --outfile %s" % (anat_file[0], new_file), shell=True)
    else:
        to_write.write('Did not save anat because %s already exists!\n' % new_file)        


def bids_fmap(base_file_id, fmap_dir, fmap_path, func_path):
    """
    Moves and converts an epi folder associated with a fieldmap
    to BIDS format. Assumes files are organized appropriately
    (e.g. "project_data/s999/ses-1/fmap-fieldmap/[examinfo]_fieldmap.jason"
          "project_data/s999/ses-1/fmap-fieldmap/[examinfo]_fieldmap.nii.gz" )
    """
    fmap_files = glob.glob(os.path.join(fmap_dir,'*.nii.gz'))
    assert len(fmap_files) == 2, "Didn't find the correct number of files in %s" % fmap_dir
    fmap_index = [0,1]['fieldmap.nii.gz' in fmap_files[1]]
    fmap_file = fmap_files[fmap_index]
    mag_file = fmap_files[1-fmap_index]
    fmap_name = base_file_id + '_' + 'fieldmap.nii.gz'
    mag_name = base_file_id + '_' + 'magnitude.nii.gz'
    if not os.path.exists(os.path.join(fmap_path, fmap_name)):
        shutil.copyfile(fmap_file, os.path.join(fmap_path, fmap_name))
        shutil.copyfile(mag_file, os.path.join(fmap_path, mag_name))
        func_runs = [os.sep.join(os.path.normpath(f).split(os.sep)[-3:]) for f in glob.glob(os.path.join(func_path,'*task*bold.nii.gz'))]
        fieldmap_meta = {'Units': 'Hz', 'IntendedFor': func_runs}
        json.dump(fieldmap_meta,open(os.path.join(fmap_path, base_file_id + '_fieldmap.json'),'w'))
    else:
        to_write.write('Did not save fmap_epi because %s already exists!\n' % os.path.join(fmap_path, fmap_name))

#bids_sbref(base_file_id, sbref_dir, func_path, bids_dir)        
def bids_sbref(base_file_id, sbref_dir, func_path, bids_dir):
    """
    Moves and converts an epi folder associated with a sbref
    calibration scan to BIDS format. Assumes tasks are organized appropriately
    (e.g. "project_data/s999/ses-1/task-rest_run-1_sbref/[examinfo].json"
          "project_data/s999/ses-1/task-rest_run-1_sbref/[examinfo].nii.gz" )    
    """
    filename = '_'.join(os.path.basename(sbref_dir).split('_'))
    sbref_files = glob.glob(os.path.join(sbref_dir,'*.nii.gz'))
    # remove files that are sometimes added, but are of no interest
    sbref_files = [i for i in sbref_files if 'phase' not in i]
    assert len(sbref_files) <= 1, "More than one func file found in directory %s" % sbref_dir
    if len(sbref_files) == 0:
        to_write.write('Skipping %s, no nii.gz file found\n' % sbref_dir)
        return

    # bring to subject directory and divide into sbref and bold
    sbref_file = clean_file(os.path.join(func_path, base_file_id + '_' + filename + '.nii.gz'))
    # check if file exists. If it does, check if the saved file has more time points
    if os.path.exists(sbref_file):
        to_write.write('%s already exists!\n' % sbref_file)
        saved_shape = nib.load(sbref_file).shape
        current_shape = nib.load(sbref_files[0]).shape
        to_write.write('Dimensions of saved image: %s\n' % list(saved_shape))
        to_write.write('Dimensions of current image: %s\n' % list(current_shape))
        if (current_shape[-1] <= saved_shape[-1]):
            to_write.write('Current image has fewer or equivalent time points than saved image. Exiting...\n')
            return
        else:
            to_write.write('Current image has more time points than saved image. Overwriting...\n')
    # save sbref image to bids directory
    shutil.copyfile(sbref_files[0], sbref_file)
    # get metadata
    sbref_meta_path = clean_file(os.path.join(bids_dir, re.sub('_run[-_][0-9]','',filename) + '.json'))
    if not os.path.exists(sbref_meta_path):
        try:
            json_file = [x for x in glob.glob(os.path.join(sbref_dir,'*.json')) 
                            if 'qa' not in x][0]
            func_meta = get_functional_meta(json_file, filename)
            json.dump(func_meta,open(sbref_meta_path,'w'))
        except IndexError:
            to_write.write("Metadata couldn't be created for %s\n" % sbref_file)        

#bids_task(base_file_id, task_dir, func_path, bids_dir)
def bids_task(base_file_id, task_dir, func_path, bids_dir):
    """
    Moves and converts an epi folder associated with a task
    to BIDS format. Assumes tasks are organized appropriately
    (e.g. "project_data/s999/ses-1/task-stopSignal_run-1_ssg/[examinfo].json"
          "project_data/s999/ses-1/task-stopSignal_run-1_ssg/[examinfo].nii.gz" ) 
    """
    taskname = '_'.join(os.path.basename(task_dir).split('_'))
    task_file = [f for f in glob.glob(os.path.join(task_dir,'*1.nii.gz')) if "fieldmap" not in f]
    assert len(task_file) <= 1, "More than one func file found in directory %s" % task_dir
    if len(task_file) == 0:
        to_write.write('Skipping %s, no nii.gz file found\n' % task_dir)
        return

    # bring to subject directory and divide into sbref and bold
    bold_file = os.path.join(func_path, base_file_id + '_' + taskname + '.nii.gz')
    bold_file = bold_file.replace('_ssg', '_bold')
    # check if file exists. If it does, check if the saved file has more time points
    if os.path.exists(bold_file):
        to_write.write('%s already exists!\n' % bold_file)
        saved_shape = nib.load(bold_file).shape
        current_shape = nib.load(task_file[0]).shape
        to_write.write('Dimensions of saved image: %s\n' % list(saved_shape))
        to_write.write('Dimensions of current image: %s\n' % list(current_shape))
        if (current_shape[-1] <= saved_shape[-1]):
            to_write.write('Current image has fewer or equal time points than saved image. Exiting...\n')
            return
        else:
            to_write.write('Current image has more time points than saved image. Overwriting...\n')
    # save bold image to bids directory
    shutil.copyfile(task_file[0], bold_file)
    # get epi metadata
    bold_meta_path = os.path.join(bids_dir, re.sub('_run[-_][0-9]','',taskname) + '_bold.json')
    bold_meta_path = clean_file(bold_meta_path)
    if not os.path.exists(bold_meta_path):
        meta_file = [x for x in glob.glob(os.path.join(task_dir,'*.json')) if 'qa' not in x][0]
        func_meta = get_functional_meta(meta_file, taskname)
        json.dump(func_meta,open(bold_meta_path,'w'))
    # get physio if it exists
    physio_file = glob.glob(os.path.join(task_dir, '*physio.zip'))
    if len(physio_file)>0:
        assert len(physio_file)==1, ("More than one physio file found in directory %s" % task_dir)
        with ZipFile(physio_file[0], 'r') as zipf:
            zipf.extractall(path=func_path)
        # extract the actual filename of the physio data
        physio_file = os.path.basename(physio_file[0])[:-4]
        for pfile in glob.iglob(os.path.join(func_path, physio_file, '*Data*')):
            pname = 'respiratory' if 'RESP' in pfile else 'cardiac'
            new_physio_file = bold_file.replace('_bold.nii.gz', 
                                '_recording-' + pname + '_physio.tsv.gz')
            f = np.loadtxt(pfile)
            np.savetxt(new_physio_file, f, delimiter = '\t')
        shutil.rmtree(os.path.join(func_path, physio_file))

# func_meta = get_functional_meta(meta_file, filename)        
def get_functional_meta(json_file, taskname):
    """
    Returns BIDS meta data for bold using JSON file
    """
    meta_file = json.load(open(json_file,'r'))
    meta_data = {}
    mux = meta_file['num_bands']
    nslices = meta_file['num_slices'] * mux
    tr = meta_file['tr']
    n_echoes = meta_file['acquisition_matrix'][0] 

    # fill in metadata
    meta_data['TaskName'] = taskname.split('_')[0]
    meta_data['EffectiveEchoSpacing'] = meta_file['effective_echo_spacing']
    meta_data['EchoTime'] = meta_file['te']
    meta_data['FlipAngle'] = meta_file['flip_angle']
    meta_data['RepetitionTime'] = tr
    # slice timing
    meta_data['SliceTiming'] = get_slice_timing(nslices, tr, mux = mux)
    total_time = (n_echoes-1)*meta_data['EffectiveEchoSpacing']
    meta_data['TotalReadoutTime'] = total_time
    meta_data['PhaseEncodingDirection'] = ['i','j','k'][meta_file['phase_encode_direction']] + '-'        
    return meta_data

def get_slice_timing(nslices, tr, mux = None, order = 'ascending'):
    """
    nslices: int, total number of slices
    tr: float, repetition total_time
    mux: int, optional mux factor
    """
    if mux:
        assert nslices%mux == 0
        nslices = nslices//mux
        mux_slice_acq_order = list(range(0,nslices,2)) + list(range(1,nslices,2))
        mux_slice_acq_time = [float(s)/nslices*tr for s in range(nslices)]
        unmux_slice_acq_order = [nslices*m+s for m in range(mux) for s in mux_slice_acq_order]
        unmux_slice_acq_time = mux_slice_acq_time * mux
        slicetimes = list(zip(unmux_slice_acq_time,unmux_slice_acq_order))
    else:
        slice_acq_order = list(range(0,nslices,2)) + list(range(1,nslices,2))
        slice_acq_time = [float(s)/nslices*tr for s in range(nslices)]
        slicetimes = list(zip(slice_acq_time,slice_acq_order))
    #reorder slicetimes by slice number
    sort_index = sorted(enumerate([x[1] for x in slicetimes]), key= lambda x: x[1])
    sorted_slicetimes = [slicetimes[i[0]][0] for i in sort_index]
    return sorted_slicetimes

def get_subj_path(fly_path, bids_dir, id_correction_dict=None):
    """
    Takes a flywheel path and returns a subject id and session #
    for an appropriate BIDS path.
    DOES NOT CURRENTLY MAKE USE OF id_correction_dict
    """
    path_pieces = fly_path.split('/')
    sub_id = path_pieces[1]
    session = path_pieces[2].split('-')[1]
    subj_path = os.path.join(bids_dir, 'sub-'+sub_id, 'ses-'+session)
    return subj_path


def mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        to_write.write('Directory %s already existed\n' % path)
    return path

# *****************************
# *** Main BIDS function
# *****************************
#bids_subj(subj_path, bids_dir, fly_path, skip_complete)
def bids_subj(subj_path, bids_dir, fly_path, skip_complete=False):
    """
    Takes a subject path (the BIDS path to the subject directory),
    a data path (the path to the BIDS directory), and a 
    fly_path (the path to the subject's data in the original format) 
    and moves/converts that subject's data to BIDS
    """
    if os.path.exists(subj_path) and skip_complete:
        to_write.write("Path %s already exists. Skipping.\n\n" % subj_path)
    else:
        to_write.write("********************************************\n")
        to_write.write("BIDSifying %s\n" % subj_path)
        to_write.write("Using flywheel path: %s\n" % fly_path)
        to_write.write("********************************************\n\n")
        # extract subject ID
        split_path = os.path.normpath(subj_path).split(os.sep)
        sub_id = [x for x in split_path if 'sub' in x][0]
        ses_id = [x for x in split_path if 'ses' in x][0]
        base_file_id = sub_id + '_' + ses_id
        # split subject path into a super subject path and a session path
        session_path = subj_path
        subj_path = os.path.split(subj_path)[0]
        mkdir(subj_path)
        mkdir(session_path)
        anat_path = mkdir(os.path.join(session_path,'anat'))
        func_path = mkdir(os.path.join(session_path,'func'))
        fmap_path = mkdir(os.path.join(session_path,'fmap'))
        # strip paths for rsync transfer
        stripped_anat_path = os.sep.join(anat_path.split(os.sep)[-3:-1])
        stripped_func_path = os.sep.join(func_path.split(os.sep)[-3:-1])
        stripped_fmap_path = os.sep.join(fmap_path.split(os.sep)[-3:-1])

        # anat files        
        to_write.write(anat_path)
        to_write.write('\nBIDSifying anatomy...\n')

        anat_dirs = sorted(glob.glob(os.path.join(fly_path,'*anat*')))[::-1]
        for anat_dir in anat_dirs:
            to_write.write('\t' + anat_dir + '\n')
            bids_anat(base_file_id, anat_dir, anat_path)

        # sbref files
        to_write.write('\nBIDSifying sbref...\n')

        sbref_dirs = sorted(glob.glob(os.path.join(fly_path,'*sbref*')))[::-1]
        for sbref_dir in sbref_dirs:
            to_write.write('\t' + sbref_dir + '\n')
            bids_sbref(base_file_id, sbref_dir, func_path, bids_dir)

        # task files        
        to_write.write('\nBIDSifying task...\n')

        task_dirs = sorted(glob.glob(os.path.join(fly_path,'*task*')))
        for task_dir in [x for x in task_dirs if 'sbref' not in x]:
            to_write.write('\t' + task_dir + '\n')
            bids_task(base_file_id, task_dir, func_path, bids_dir)

        # cleanup
        cleanup(func_path)

        # fmap files
        to_write.write('\nBIDSifying fmap...\n')

        fmap_dirs = sorted(glob.glob(os.path.join(fly_path,'*fieldmap*')))[::-1]
        for fmap_dir in fmap_dirs:
            to_write.write('\t' + fmap_dir + '\n')
            bids_fmap(base_file_id, fmap_dir, fmap_path, func_path)

   
        
# *****************************
# *** ORGANIZE IN BIDS
# *****************************

# parse arguments
parser = argparse.ArgumentParser(description='fMRI Analysis Entrypoint Script.')

parser.add_argument('fly_dir', help='Directory of the non-BIDS fmri data')
parser.add_argument('bids_dir', help='Directory of the BIDS fmri data')
parser.add_argument('--study_id', default=None, help='Study ID. If not supplied, the directory above the fly_dir will be used')
parser.add_argument('--id_correction', help='JSON file that lists subject id corrections for fmri scan IDs')
parser.add_argument('--fly_paths', nargs='+', default=None)
parser.add_argument('--write_out', default=None)
parser.add_argument('--skip_complete', action='store_true')
args, unknown = parser.parse_known_args()


# directory with flywheel data
fly_dir = args.fly_dir
# bids directory
bids_dir = args.bids_dir
#study name
if args.study_id:
    study_id == args.study_id
else:
    study_id = fly_dir.strip(os.sep).split(os.sep)[0]
# write_out
if args.write_out:
    write_out = args.write_out
else:
    write_out = '%s_FLY_to_BIDS.txt' % study_id
to_write = open(write_out, 'w')
# whether to skip completed scans or check again
skip_complete = args.skip_complete

# set id_correction_dict if provided
id_correction_dict = None
if args.id_correction:
    id_correction_dict = json.load(open(args.id_correction,'r'))
    to_write.write('Using ID correction json file: %s\n' % args.id_correction)
#header file
header = {'Name': study_id, 'BIDSVersion': '1.1-rc1'}
json.dump(header,open(os.path.join(bids_dir, 'dataset_description.json'),'w'))
# error file
error_file = os.path.join(bids_dir, 'error_record.txt')

# bidsify all subjects in path
if args.fly_paths is None:
    fly_paths = glob.glob(os.path.join(fly_dir, '*/*'))
else:
    fly_paths = [glob.glob(os.path.join(fly_dir, '*'+path))[0] for path in args.fly_paths]
    


####################################################
##################TESTING CONSTANTS#################
#fly_dir = 'uh2aim4'
#bids_dir = 'uh2aim4_BIDS'
#study_id = fly_dir.strip(os.sep).split(os.sep)[0]
#write_out = '%s_FLY_to_BIDS.txt' % study_id
#to_write = open(write_out, 'w')
#skip_complete = True
#id_correction_dict = None
#header = {'Name': study_id, 'BIDSVersion': '1.1-rc1'}
#json.dump(header,open(os.path.join(bids_dir, 'dataset_description.json'),'w'))
#error_file = os.path.join(bids_dir, 'error_record.txt')
#fly_paths = glob.glob(os.path.join(fly_dir, '*/*'))
#i=0
#fly_path = fly_paths[0]
####################################################    
    
for i, fly_path in enumerate(sorted(fly_paths)):
    to_write.write("BIDSifying path %s out of %s\n" % (str(i+1), str(len(fly_paths))))
    subj_path  = get_subj_path(fly_path, bids_dir, id_correction_dict)
    print('Subject Path: ', subj_path)
    if subj_path == None:
        to_write.write("Couldn't find subj_path for %s\n" % fly_path)
        with open(error_file, 'a') as filey:
            filey.write("Couldn't find subj_path for %s\n" % fly_path)
        continue
    bids_subj(subj_path, bids_dir, fly_path, skip_complete)
    to_write.flush()

# add physio metadata
if not os.path.exists(os.path.join(bids_dir, 'recording-cardiac_physio.json')):
    if len(glob.glob(os.path.join(bids_dir, 'sub-*', 'ses-*', 'func', '*cardiac*'))) > 0:
        json.dump(cardiac_bids, open(os.path.join(bids_dir, 'recording-cardiac_physio.json'),'w'))
if not os.path.exists(os.path.join(bids_dir, 'recording-respiratory_physio.json')):
    if len(glob.glob(os.path.join(bids_dir, 'sub-*', 'ses-*', 'func', '*respiratory*'))) > 0:
        json.dump(respiratory_bids, open(os.path.join(bids_dir, 'recording-respiratory_physio.json'),'w'))

# *****************************
# *** Cleanup
# *****************************
cleanup(bids_dir)
to_write.close()