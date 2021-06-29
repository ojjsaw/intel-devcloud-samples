#!/usr/bin/env python3.7

#echo "$(cat $1)"
device=$(cut -d "," -f 1  $1 |xargs)
FP16=$(cut -d "," -f 2  $1 | xargs) 
FP32=$(cut -d "," -f 3  $1 | xargs)
ver=$(cut -d "," -f 4  $1 | xargs)
openvino_ver=openvino_$ver

echo "Openvino Package Version: $openvino_ver"
echo "Device:  $device"
echo "Precision:  $FP16, $FP32"


INPUT_FILE="/opt/intel/$openvino_ver/python/samples/object-detection-python/cars_1900.mp4"
DEVICE=$device
NUM_REQS=2
Output_folder_32="data/output_obj_det/results/FP32"
Output_folder_16="data/output_obj_det/results/FP16"
XML_IR_FP16="data/output_obj_det/IR/FP16"
XML_IR_FP32="data/output_obj_det/IR/FP32"

IR_FP16="$XML_IR_FP16/mobilenet-ssd.xml"
IR_FP32="$XML_IR_FP32/mobilenet-ssd.xml"

Sample_name="object_detection.py"

SCALE_FRAME_RATE=1    # scale number or output frames to input frames
SCALE_RESOLUTION=0.5  # scale output frame resolution



source /opt/intel/$openvino_ver/bin/setupvars.sh

python3 -V
python3 /opt/intel/$openvino_ver/deployment_tools/tools/model_downloader/downloader.py  --name mobilenet-ssd -o raw_models 

if [[ ! -z "$FP16" ]];
then
   echo "Creating output folder \$FP16"
   mkdir -p $Output_folder_16 
   mkdir -p $XML_IR_FP16 
   python3 /opt/intel/$openvino_ver/deployment_tools/model_optimizer/mo.py \
   --input_model raw_models/public/mobilenet-ssd/mobilenet-ssd.caffemodel \
   --data_type $FP16 \
   --output_dir $XML_IR_FP16 \
   --scale 256 \
   --mean_values [127,127,127]

   python3 $Sample_name  -i $INPUT_FILE  -m  $IR_FP16  --labels labels.txt -o $Output_folder_16 -d $device -nireq $NUM_REQS

   python3 object_detection_annotate.py -i $INPUT_FILE \
                                     -o $Output_folder_16 \
                                     -f $SCALE_FRAME_RATE \
                                     -s $SCALE_RESOLUTION


fi

if [[ ! -z "$FP32" ]];
then
   echo "Creating output folder \$FP32"
   mkdir -p $Output_folder_32
   mkdir -p $XML_IR_FP32
   python3 /opt/intel/$openvino_ver/deployment_tools/model_optimizer/mo.py \
   --input_model raw_models/public/mobilenet-ssd/mobilenet-ssd.caffemodel \
   --data_type $FP32 \
   --output_dir $XML_IR_FP32 \
   --scale 256 \
   --mean_values [127,127,127]

   python3 $Sample_name  -i $INPUT_FILE  -m  $IR_FP32  --labels labels.txt -o $Output_folder_32 -d $device -nireq $NUM_REQS

   python3 object_detection_annotate.py -i $INPUT_FILE \
                                     -o $Output_folder_32 \
                                     -f $SCALE_FRAME_RATE \
                                     -s $SCALE_RESOLUTION

fi


