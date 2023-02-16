const webpack = require('webpack');

module.exports = {
  mode: process.env.NODE_ENV === 'development' ? 'development' : 'production',
  entry: './heatmap.js',
  output: {
    path: '../app/static/',
    filename: 'heatmap.js'
  },
};
