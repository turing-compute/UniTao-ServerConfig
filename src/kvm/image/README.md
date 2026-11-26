# Image utility of KVM
Operate on image for vm

### Description
This Image utility can be used to
 - Download image from link
 - Create image with specified format and size
 - Create image from base image

 ### Based on commands
 - wget to download image from remote link
 - qemu-img to create new image with baseline, size and format


 ### schema

 ```jsonc
{
    "imagePath": "",                    // image file path to store the image
    "imageSource": "{remote/local}",    // source of image decide how image is being created
                                        // remote: download from remote site
                                        // local: locally created with/without base image
    "imageFormat": "{qcow2/img}",       // the image file type  
    "downloadLink": "",                 // optional, only use when need to download the image file from remote location
    "sizeInGB": 30,                     // optional, only use to create a new image with size requirement
                                        // after Create() it is always recorded (virtual size, GB rounded up)
    "baseImagePath": "",                // optional, only use when need to create a new image with a baseline image
    "baseImageFormat": "{qcow2/img}",
    
}
 ```