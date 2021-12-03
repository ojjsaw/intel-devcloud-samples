# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

# ==============================================================================
# Copyright (C) 2021 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# Modified from tensorflow object detection examples:
# https://github.com/tensorflow/tensorflow/blob/master/tensorflow/examples/label_image/label_image.py
# https://github.com/mystic123/tensorflow-yolo-v3/blob/master/utils.py

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import argparse
import numpy as np
import tensorflow as tf
import openvino_tensorflow as ovtf
import time
import cv2
from PIL import Image, ImageFont, ImageDraw


def load_graph(model_file):
    graph = tf.Graph()
    graph_def = tf.compat.v1.GraphDef()
    assert os.path.exists(model_file), "Could not find model path"
    with open(model_file, "rb") as f:
        graph_def.ParseFromString(f.read())
    with graph.as_default():
        tf.import_graph_def(graph_def)

    return graph


def letter_box_image(image_path, input_height, input_width,
                     fill_value) -> np.ndarray:
    image = Image.open(image_path)
    height_ratio = float(input_height) / image.size[1]
    width_ratio = float(input_width) / image.size[0]
    fit_ratio = min(width_ratio, height_ratio)
    fit_height = int(image.size[1] * fit_ratio)
    fit_width = int(image.size[0] * fit_ratio)
    fit_image = np.asarray(
        image.resize((fit_width, fit_height), resample=Image.BILINEAR))

    fill_value = np.full(fit_image.shape[2], fill_value, fit_image.dtype)
    to_return = np.tile(fill_value, (input_height, input_width, 1))
    pad_top = int(0.5 * (input_height - fit_height))
    pad_left = int(0.5 * (input_width - fit_width))
    to_return[pad_top:pad_top + fit_height, pad_left:pad_left +
              fit_width] = fit_image

    return to_return, image


def load_coco_names(file_name):
    names = {}
    assert os.path.exists(file_name), "path doesn't exist {0}".format(file_name)
    with open(file_name) as f:
        for coco_id, name in enumerate(f):
            names[coco_id] = name
    return names


def letter_box_pos_to_original_pos(letter_pos, current_size,
                                   ori_image_size) -> np.ndarray:
    letter_pos = np.asarray(letter_pos, dtype=np.float)
    current_size = np.asarray(current_size, dtype=np.float)
    ori_image_size = np.asarray(ori_image_size, dtype=np.float)
    final_ratio = min(current_size[0] / ori_image_size[0],
                      current_size[1] / ori_image_size[1])
    pad = 0.5 * (current_size - final_ratio * ori_image_size)
    pad = pad.astype(np.int32)
    to_return_pos = (letter_pos - pad) / final_ratio
    return to_return_pos


def convert_to_original_size(box, size, original_size, is_letter_box_image):
    if is_letter_box_image:
        box = box.reshape(2, 2)
        box[0, :] = letter_box_pos_to_original_pos(box[0, :], size,
                                                   original_size)
        box[1, :] = letter_box_pos_to_original_pos(box[1, :], size,
                                                   original_size)
    else:
        ratio = original_size / size
        box = box.reshape(2, 2) * ratio
    return list(box.reshape(-1))


def draw_boxes(boxes, img, cls_names, detection_size, is_letter_box_image):
    draw = ImageDraw.Draw(img)
    for cls, bboxs in boxes.items():
        color = (256, 256, 256)
        for box, score in bboxs:
            box = convert_to_original_size(box, np.array(detection_size),
                                           np.array(img.size),
                                           is_letter_box_image)
            draw.rectangle(box, outline=color)
            draw.text(
                box[:2],
                '{} {:.2f}%'.format(cls_names[cls], score * 100),
                fill=color)

    # converting PIL image back to OpenCV format
    im_np = np.asarray(img)
    im_np = cv2.cvtColor(im_np, cv2.COLOR_RGB2BGR)
    return im_np


def iou(box1, box2):
    b1_x0, b1_y0, b1_x1, b1_y1 = box1
    b2_x0, b2_y0, b2_x1, b2_y1 = box2

    int_x0 = max(b1_x0, b2_x0)
    int_y0 = max(b1_y0, b2_y0)
    int_x1 = min(b1_x1, b2_x1)
    int_y1 = min(b1_y1, b2_y1)

    int_area = (int_x1 - int_x0) * (int_y1 - int_y0)

    b1_area = (b1_x1 - b1_x0) * (b1_y1 - b1_y0)
    b2_area = (b2_x1 - b2_x0) * (b2_y1 - b2_y0)

    iou = int_area / (b1_area + b2_area - int_area + 1e-05)

    return iou


def non_max_suppression(predictions_with_boxes,
                        confidence_threshold,
                        iou_threshold=0.4):
    conf_mask = np.expand_dims(
        (predictions_with_boxes[:, :, 4] > confidence_threshold), -1)
    predictions = predictions_with_boxes * conf_mask

    result = {}
    for i, image_pred in enumerate(predictions):
        shape = image_pred.shape
        non_zero_idxs = np.nonzero(image_pred)
        image_pred = image_pred[non_zero_idxs]
        image_pred = image_pred.reshape(-1, shape[-1])

        bbox_attrs = image_pred[:, :5]
        classes = image_pred[:, 5:]
        classes = np.argmax(classes, axis=-1)

        unique_classes = list(set(classes.reshape(-1)))

        for cls in unique_classes:
            cls_mask = classes == cls
            cls_boxes = bbox_attrs[np.nonzero(cls_mask)]
            cls_boxes = cls_boxes[cls_boxes[:, -1].argsort()[::-1]]
            cls_scores = cls_boxes[:, -1]
            cls_boxes = cls_boxes[:, :-1]

            while len(cls_boxes) > 0:
                box = cls_boxes[0]
                score = cls_scores[0]
                if cls not in result:
                    result[cls] = []
                result[cls].append((box, score))
                cls_boxes = cls_boxes[1:]
                # iou threshold check for overlapping boxes
                ious = np.array([iou(box, x) for x in cls_boxes])
                iou_mask = ious < iou_threshold
                cls_boxes = cls_boxes[np.nonzero(iou_mask)]
                cls_scores = cls_scores[np.nonzero(iou_mask)]

    return result
def run_video_detect(model_file, input_layer, output_layer,label_file,input_file,input_height,input_width, input_mean,input_std,filename, backend_name,output_filename):
    if label_file:
        classes = load_coco_names(label_file)
    cap = cv2.VideoCapture(input_file)
    video_writer = cv2.VideoWriter()

    output_resolution = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT )))
    
    if output_dir:
        output_filename = output_dir+"/"+backend_name+"_"+output_filename
    else:
        output_filename = backend_name+"_"+output_filename
   
    video_writer.open(output_filename, cv2.VideoWriter_fourcc(*'avc1'), 20.0, output_resolution)
    frame_count = 0
    curr_fps = 0
    config = tf.compat.v1.ConfigProto()
    with tf.compat.v1.Session(graph=graph, config=config) as sess:
        while cap.isOpened():
            ret, frame = cap.read()
            if ret is True:
                # pre-processing steps
                img = frame
                #img_resized = cv2.resize(frame, (input_height, input_width))
                # Run
                frameID = cap.get(cv2.CAP_PROP_POS_FRAMES)
                start = time.time()
                if(backend_name == "VAD-M"):
                    xlist = []
                    for i in range(1, 9):
                        img_resized = cv2.resize(frame, (input_height, input_width))
                        xlist.append(img_resized)
                    x = np.stack(xlist)
                else:
                    x = cv2.resize(frame, (input_height, input_width))
                    x = [x]
                detected_boxes = sess.run(
                    output_operation.outputs[0],
                    {input_operation.outputs[0]: x})
                elapsed = time.time() - start
                fps = 1 / elapsed
                curr_fps += fps
                frame_count += 1
                # post-processing - apply non max suppression, draw boxes and save updated image
                filtered_boxes = non_max_suppression(
                    detected_boxes, conf_threshold, iou_threshold)

                # OpenCV frame to PIL format conversions as the draw_box function uses PIL
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                im_pil = Image.fromarray(img)

                # modified draw_boxes function to return an openCV formatted image
                img_bbox = draw_boxes(filtered_boxes, im_pil, classes,
                                      (input_width, input_height), True)

                # draw information overlay onto the frames
                cv2.putText(img_bbox,
                            'Inference Running on : {0}'.format(backend_name),
                            (30, 50), font, font_size, color, font_thickness)
                cv2.putText(
                    img_bbox, 'FPS : {0} | Inference Time : {1}ms'.format(
                        int(fps), round((elapsed * 1000), 2)), (30, 80), font,
                    font_size, color, font_thickness)
                #cv2.imshow("detections", img_bbox)
                video_writer.write(img_bbox)
                if cv2.waitKey(1) & 0XFF == ord('q'):
                    break
            else:
                avg_fps = float(curr_fps/frame_count)
                
              
                print("Completed")

               # f = open(result_file_name, "w")
                if(backend_name == "VAD-M"):
                    avg_fps = 8*avg_fps
                    #print(avg_fps)
                print("Inference Time: ms",float(1000/avg_fps))
               
                break
    video_writer.release()
    cap.release()
    cv2.destroyAllWindows()

def run_image_detect(model_file, input_layer, output_layer,label_file,input_file,input_height,input_width, input_mean,input_std, filename, backend_name, output_filename):
    img_resized, img = letter_box_image(input_file, input_height, input_width,
                                        128)
    img_resized = img_resized.astype(np.float32)
    if label_file:
        labels = load_coco_names(label_file)
    config = tf.compat.v1.ConfigProto()
    with tf.compat.v1.Session(graph=graph, config=config) as sess:
        # Warmup
        detected_boxes = sess.run(output_operation.outputs[0],
                                  {input_operation.outputs[0]: [img_resized]})
        # Run
        import time
        start = time.time()
        detected_boxes = sess.run(output_operation.outputs[0],
                                  {input_operation.outputs[0]: [img_resized]})
        elapsed = time.time() - start
        print('Inference time in ms: %f' % (elapsed * 1000))
        
        result_file_name = "/mount_folder/" +"performance.txt"
       # assert os.path.isdir("results"), "Could not find results folder"
        f = open(result_file_name, "w")
        fps = 1/elapsed
        f.write('Throughput: {:.3g} FPS \n'.format(fps))
        f.write('Latency: {:.3f} ms\n'.format(elapsed*1000))
        f.close()
    # apply non max suppresion, draw boxes and save updated image
    filtered_boxes = non_max_suppression(detected_boxes, conf_threshold,
                                         iou_threshold)
    draw_boxes(filtered_boxes, img, labels, (input_width, input_height), True)
    if output_dir:
        img.save(os.path.join(output_dir, output_filename))
    else:
        img.save(output_filename)

if __name__ == "__main__":
    input_file = "../data/images/grace_hopper.jpg"
    model_file = "frozen_darknet_yolov3_model.pb"
    label_file = "../data/coco.names"
    input_height = 160
    input_width = 160
    input_mean = 0
    input_std = 255
    input_layer = "inputs"
    output_layer = "output_boxes"
    backend_name = "CPU"
    output_dir = "."
    conf_threshold = 0.6
    iou_threshold = 0.5
    output_filename = "output.mp4"
    # overlay parameters
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_size = .6
    color = (0, 0, 0)
    font_thickness = 2

    parser = argparse.ArgumentParser()
    parser.add_argument("-m","--graph", help="graph/model to be executed")
    parser.add_argument("-i","--input_layer", help="name of input layer")
    parser.add_argument("-o","--output_layer", help="name of output layer")
    parser.add_argument("-l","--labels", help="name of file containing labels")
    parser.add_argument(
        "-ip","--input",
        help="input (0 - for camera / absolute video file path) to be processed"
    )
    parser.add_argument("-ih","--input_height", type=int, help="input height")
    parser.add_argument("-iw","--input_width", type=int, help="input width")
    parser.add_argument("-im","--input_mean", type=int, help="input mean")
    parser.add_argument("-is","--input_std", type=int, help="input std")
    parser.add_argument("-d","--backend", help="name of backend. Default is CPU")
    parser.add_argument(
        "--output_dir",
        help="Directory that stores updated image."
        " Default is directory from where this sample is launched.")
    parser.add_argument(
        "--conf_threshold",
        type=float,
        help="confidence threshold. Default is 0.6")
    parser.add_argument(
        "--iou_threshold", type=float, help="iou threshold. Default is 0.5")
    parser.add_argument("-f","--flag", help="disable backend")
    parser.add_argument("-it",
    "--input_type",
        help="input type either video or image"
    )
    parser.add_argument("-of","--output_filename", help="outputfilename for output detections video")
    args = parser.parse_args()
    if args.graph:
        model_file = args.graph
        if not args.input_layer:
            raise Exception("Specify input layer for this network")
        else:
            input_layer = args.input_layer
        if not args.output_layer:
            raise Exception("Specify output layer for this network")
        else:
            output_layer = args.output_layer
        if args.labels:
            label_file = args.labels
        else:
            label_file = None
    if args.input:
        input_file = args.input
    if args.input_height:
        input_height = args.input_height
    if args.input_width:
        input_width = args.input_width
    if args.input_mean:
        input_mean = args.input_mean
    if args.input_std:
        input_std = args.input_std
    if args.backend:
        backend_name = args.backend
    if args.output_dir:
        output_dir = args.output_dir
    if args.conf_threshold:
        conf_threshold = args.conf_threshold
    if args.iou_threshold:
        iou_threshold = args.iou_threshold
    if not label_file:
            raise Exception("Specify label map file")
    if args.output_filename:
        output_filename = args.output_filename
    # Load graph and process input image
    graph = load_graph(model_file)
    # Load the labels
    if label_file:
        labels = load_coco_names(label_file)
    input_name = "import/" + input_layer
    output_name = "import/" + output_layer
    input_operation = graph.get_operation_by_name(input_name)
    output_operation = graph.get_operation_by_name(output_name)
    flag_enable = args.flag
    flag_input = args.input_type
    filename = ""
    # Print list of available backends
    if(flag_enable == "native"):
        print('StockTensorflow')
        filename = "native"
        ovtf.disable()
    elif(flag_enable == "oneDNN"):
        print('oneDNN optimized')
        filename = "oneDNN"
        ovtf.disable()
        os.environ['TF_ENABLE_ONEDNN_OPTS']='1'
    elif(flag_enable == "openvino"):
        filename = "openvino"
        print('Openvino Integration With Tensorflow')
        print('Available Backends:')
        backends_list = ovtf.list_backends()
        for backend in backends_list:
            print(backend)
        os.environ['TF_ENABLE_ONEDNN_OPTS']='1'
        ovtf.set_backend(backend_name)
    else:
        raise AssertionError("flag_enable string not supported")
        
    assert os.path.exists(input_file), "Could not find video file path"
    if(flag_input == "video"):
        run_video_detect(model_file, input_layer, output_layer,label_file,input_file,input_height,input_width, input_mean,input_std, filename, backend_name, output_filename)
    elif(flag_input == "image"):
        run_image_detect(model_file, input_layer, output_layer,label_file,input_file,input_height,input_width, input_mean,input_std, filename, backend_name, output_filename)
    else:
        raise AssertionError("flag input type string not supported")
    
