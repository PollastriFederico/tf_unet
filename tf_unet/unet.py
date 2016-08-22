# Copyright (C) 2015 ETH Zurich, Institute for Astronomy

'''
Created on Jul 28, 2016

author: jakeret
'''
from __future__ import print_function, division, absolute_import, unicode_literals

import os
import shutil
import numpy as np

import tensorflow as tf

from tf_unet import util
from tf_unet.layers import (weight_variable, weight_variable_devonc, bias_variable, 
                            conv2d, deconv2d, max_pool, crop_and_concat, pixel_wise_softmax_2,
                            cross_entropy)


def create_conv_net(x, keep_prob, channels, n_class, layers=3, features_root=16, filter_size=3, pool_size=2, summaries=True):
    print("Layers {layers}, features {features}, filter size {filter_size}x{filter_size}, pool size: {pool_size}x{pool_size}".format(layers=layers,
                                                                                                           features=features_root,
                                                                                                           filter_size=filter_size,
                                                                                                           pool_size=pool_size))
    # Placeholder for the input image
    nx = tf.shape(x)[1]
    ny = tf.shape(x)[2]
    x_image = tf.reshape(x, tf.pack([-1,nx,ny,channels]))
    in_node = x_image
    batch_size = tf.shape(x_image)[0]
 
    weights = []
    biases = []
    convs = []
    pools = {}
    deconv = {}
    dw_h_convs = {}
    up_h_convs = {}
    
    stddev = np.sqrt(2 / (filter_size**2 * features_root))
    # down layers
    for layer in range(0, layers):
        features = 2**layer*features_root
        if layer == 0:
            w1 = weight_variable([filter_size, filter_size, channels, features], stddev)
        else:
            w1 = weight_variable([filter_size, filter_size, features//2, features], stddev)
            
        w2 = weight_variable([filter_size, filter_size, features, features], stddev)
        b1 = bias_variable([features])
        b2 = bias_variable([features])
        
        conv1 = conv2d(in_node, w1, keep_prob)
        tmp_h_conv = tf.nn.relu(conv1 + b1)
        conv2 = conv2d(tmp_h_conv, w2, keep_prob)
        dw_h_convs[layer] = tf.nn.relu(conv2 + b2)
        
        weights.append((w1, w2))
        biases.append((b1, b2))
        convs.append((conv1, conv2))
        
        if layer < layers-1:
            pools[layer] = max_pool(dw_h_convs[layer], pool_size)
            in_node = pools[layer]
        
    in_node = dw_h_convs[layers-1]
        
    # up layers
    for layer in range(layers-2, -1, -1):
        features = 2**(layer+1)*features_root
        
        wd = weight_variable_devonc([pool_size, pool_size, features//2, features], stddev)
        bd = bias_variable([features//2])
        h_deconv = tf.nn.relu(deconv2d(in_node, wd, pool_size) + bd)
        h_deconv_concat = crop_and_concat(dw_h_convs[layer], h_deconv, [batch_size])
        deconv[layer] = h_deconv_concat
        
        w1 = weight_variable([filter_size, filter_size, features, features//2], stddev)
        w2 = weight_variable([filter_size, filter_size, features//2, features//2], stddev)
        b1 = bias_variable([features//2])
        b2 = bias_variable([features//2])
        
        conv1 = conv2d(h_deconv_concat, w1, keep_prob)
        h_conv = tf.nn.relu(conv1 + b1)
        conv2 = conv2d(h_conv, w2, keep_prob)
        in_node = tf.nn.relu(conv2 + b2)
        up_h_convs[layer] = in_node

        weights.append((w1, w2))
        biases.append((b1, b2))
        convs.append((conv1, conv2))

    # Output Map
    weight = weight_variable([1, 1, features_root, n_class], stddev)
    bias = bias_variable([n_class])
    conv = conv2d(in_node, weight, tf.constant(1.0))
    output_map = tf.nn.relu(conv + bias)
    up_h_convs[-1] = output_map
    
    if summaries:
        for i, (c1, c2) in enumerate(convs):
            tf.image_summary('summary_conv_%02d_01'%i, get_image_summary(c1))
            tf.image_summary('summary_conv_%02d_02'%i, get_image_summary(c2))
            
        for k in sorted(pools.keys()):
            tf.image_summary('summary_pool_%02d'%k, get_image_summary(pools[k]))
        
        for k in sorted(deconv.keys()):
            tf.image_summary('summary_deconv_concat_%02d'%k, get_image_summary(deconv[k]))
            
#         for k in sorted(dw_h_convs.keys()):
#             tf.histogram_summary("dw_convolution_%02d"%k + '/activations', dw_h_convs[k])

#         for k in sorted(up_h_convs.keys()):
#             tf.histogram_summary("up_convolution_%02d"%k + '/activations', up_h_convs[k])
            
        
    return output_map


class Unet(object):
    
    def __init__(self, nx=None, ny=None, channels=3, n_class=2, **kwargs):
        print("Tensorflow version: %s"%tf.__version__)
        self.n_class = n_class
        
        self.x = tf.placeholder("float", shape=[None, nx, ny, channels])
        self.y = tf.placeholder("float", shape=[None, None, None, n_class])
        self.keep_prob = tf.placeholder(tf.float32) #dropout (keep probability)
        
        logits = create_conv_net(self.x, self.keep_prob, channels, n_class, **kwargs)
        loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(tf.reshape(logits, [-1, n_class]), 
                                                                           tf.reshape(self.y, [-1, n_class])))
#         
#         loss = tf.reduce_mean(cross_entropy(tf.reshape(self.y, [-1, n_class]),
#                                             tf.reshape(pixel_wise_softmax_2(logits), [-1, n_class]), 
#                                             ))
        
        reg_losses = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
        reg_constant = 0.001  # Choose an appropriate one.
        
        self.cost = loss + reg_constant * sum(reg_losses)
        self.predicter = pixel_wise_softmax_2(logits)
        self.correct_pred = tf.equal(tf.argmax(self.predicter, 3), tf.argmax(self.y, 3))
        self.accuracy = tf.reduce_mean(tf.cast(self.correct_pred, tf.float32))
        tf.scalar_summary('accuracy', self.accuracy)
        

    def predict(self, model_path, x_test):
        
        init = tf.initialize_all_variables()
        with tf.Session() as sess:
            # Initialize variables
            sess.run(init)
        
            # Restore model weights from previously saved model
            self.restore(sess, model_path)
            
            y_dummy = np.empty((x_test.shape[0], x_test.shape[1], x_test.shape[2], self.n_class))
            prediction = sess.run(self.predicter, feed_dict={self.x: x_test, self.y: y_dummy, self.keep_prob: 1.})
            
        return prediction
    
    def save(self, sess, model_path):
        saver = tf.train.Saver()
        save_path = saver.save(sess, model_path)
        return save_path
    
    def restore(self, sess, model_path):
        saver = tf.train.Saver()
        saver.restore(sess, model_path)
        print("Model restored from file: %s" % model_path)

class Trainer(object):
    
    prediction_path = "prediction"
    
    def __init__(self, net, batch_size=1, momentum=0.9, learning_rate=0.2, decay_rate=0.95):
        self.net = net
        self.batch_size = batch_size
        self.momentum = momentum
        self.learning_rate = learning_rate
        self.decay_rate = decay_rate
        
    def _initialize(self, training_iters, output_path, restore):
        global_step = tf.Variable(0)
        self.learning_rate = tf.train.exponential_decay(learning_rate=self.learning_rate, 
                                                        global_step=global_step, 
                                                        decay_steps=training_iters,  
                                                        decay_rate=self.decay_rate, 
                                                        staircase=True)
        
        tf.scalar_summary('learning_rate', self.learning_rate)
        tf.scalar_summary('loss', self.net.cost)
        
        self.optimizer = tf.train.MomentumOptimizer(learning_rate=self.learning_rate, 
                                                    momentum=self.momentum).minimize(self.net.cost, 
                                                                                     global_step=global_step)
                                                    
#         self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate, 
#                                                 beta1=0.9, 
#                                                 beta2=0.999, 
#                                                 epsilon=1e-08, 
#                                                 use_locking=False, 
#                                                 name='Adam').minimize(self.net.cost,
#                                                                       global_step=global_step)
                                                                                     

        self.summary_op = tf.merge_all_summaries()        
        init = tf.initialize_all_variables()
        
        if not restore:
            shutil.rmtree(self.prediction_path, ignore_errors=True)
            shutil.rmtree(output_path, ignore_errors=True)
        
        if not os.path.exists(self.prediction_path):
            os.mkdir(self.prediction_path)
        
        if not os.path.exists(output_path):
            os.mkdir(output_path)
        
        return init

    def train(self, data_provider, output_path, training_iters=10, epochs=100, dropout=0.75, display_step=1, restore=False):
        save_path = os.path.join(output_path, "model.cpkt")
        if epochs == 0:
            return save_path
        
        init = self._initialize(training_iters, output_path, restore)
        
        
        with tf.Session() as sess:
            sess.run(init)
            
            if restore:
                ckpt = tf.train.get_checkpoint_state(output_path)
                if ckpt and ckpt.model_checkpoint_path:
                    self.net.restore(sess, ckpt.model_checkpoint_path)
            
            test_x, test_y = data_provider(4)
            pred_shape = self.store_prediction(sess, test_x, test_y, "_init")
            
            summary_writer = tf.train.SummaryWriter(output_path, graph=sess.graph)
            print("Start optimization")
            
            for epoch in range(epochs):
                total_loss = 0
                for step in range((epoch*training_iters), ((epoch+1)*training_iters)):
                    batch_x, batch_y = data_provider(self.batch_size)
                     
                    # Run optimization op (backprop)
                    _, loss, lr = sess.run((self.optimizer, self.net.cost, self.learning_rate), feed_dict={self.net.x: batch_x,  
                                                                    self.net.y: util.crop_to_shape(batch_y, pred_shape),
                                                                    self.net.keep_prob: dropout})
                    
                    if step % display_step == 0:
                        self.output_minibatch_stats(sess, summary_writer, step, batch_x, util.crop_to_shape(batch_y, pred_shape))
                        
                    total_loss += loss

                self.output_epoch_stats(epoch, total_loss, training_iters, lr)
                self.store_prediction(sess, test_x, test_y, "epoch_%s"%epoch)
                    
                save_path = self.net.save(sess, save_path)
            print("Optimization Finished!")
            
            return save_path
        
    def store_prediction(self, sess, batch_x, batch_y, name):
        prediction = sess.run(self.net.predicter, feed_dict={self.net.x: batch_x, 
                                                             self.net.y: batch_y, 
                                                             self.net.keep_prob: 1.})
        pred_shape = prediction.shape
        
        loss = sess.run(self.net.cost, feed_dict={self.net.x: batch_x, 
                                                       self.net.y: util.crop_to_shape(batch_y, pred_shape), 
                                                       self.net.keep_prob: 1.})
        
        print("Prediction error= {:.1f}%, loss= {:.4f}".format(error_rate(prediction,
                                                                          util.crop_to_shape(batch_y,
                                                                                             prediction.shape)),
                                                                          loss))
              
        img = util.combine_img_prediction(batch_x, batch_y, prediction)
        util.save_image(img, "%s/%s.jpg"%(self.prediction_path, name))
        
        return pred_shape
    
    def output_epoch_stats(self, epoch, total_loss, training_iters, lr):
        print("Epoch {:}, Average loss: {:.4f}, learning rate: {:.4f}".format(epoch, (total_loss / training_iters), lr))
    
    def output_minibatch_stats(self, sess, summary_writer, step, batch_x, batch_y):
        # Calculate batch loss and accuracy
        summary_str, loss, acc, predictions = sess.run([self.summary_op, 
                                                            self.net.cost, 
                                                            self.net.accuracy, 
                                                            self.net.predicter], 
                                                           feed_dict={self.net.x: batch_x,
                                                                      self.net.y: batch_y,
                                                                      self.net.keep_prob: 1.})
        summary_writer.add_summary(summary_str, step)
        summary_writer.flush()
        print("Iter {:}, Minibatch Loss= {:.4f}, Training Accuracy= {:.4f}, Minibatch error= {:.1f}%".format(step,
                                                                                                            loss,
                                                                                                            acc,
                                                                                                            error_rate(predictions, batch_y)))


def error_rate(predictions, labels):
    """Return the error rate based on dense predictions and 1-hot labels."""
    return 100.0 - (
        100.0 *
        np.sum(np.argmax(predictions, 3) == np.argmax(labels, 3)) /
        (predictions.shape[0]*predictions.shape[1]*predictions.shape[2]))


def get_image_summary(img, idx=0):
    """Make an image summary for 4d tensor image with index idx"""
    V = tf.slice(img, (0, 0, 0, idx), (1, -1, -1, 1))
    V -= tf.reduce_min(V)
    V /= tf.reduce_max(V)
    V *= 255
    
    img_w = tf.shape(img)[1]
    img_h = tf.shape(img)[2]
    V = tf.reshape(V, tf.pack((img_w, img_h, 1)))
    V = tf.transpose(V, (2, 0, 1))
    V = tf.reshape(V, tf.pack((-1, img_w, img_h, 1)))
    return V
